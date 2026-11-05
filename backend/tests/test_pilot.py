"""Pilote de bout en bout, Anthropic et ElevenLabs simulés au niveau HTTP
(les vrais SDK sérialisent les requêtes et décodent les réponses)."""

import base64
import json
import math
import re
from array import array

import httpx
import httpx2  # le SDK Anthropic 2.x utilise httpx2
import pytest

import datagen.voices as voices_mod
from datagen.__main__ import main
from datagen.plan import STATES


def fake_dialogue_from_brief(brief: str) -> dict:
    """Dialogue valide construit à partir des contraintes du brief."""
    objections = re.search(r"in this order: (.*)\.", brief).group(1)
    objections = [] if objections.startswith("none") else [o.strip() for o in objections.split(",")]
    arc = re.search(r"\(start -> middle -> end\): (.*)\.", brief).group(1).split(" -> ")
    outcome = re.search(r"Outcome: (\w+)\.", brief).group(1)
    words = int(re.search(r"about (\d+) spoken words", brief).group(1))

    def t(speaker, text, step, emotion="neutral", objection=None):
        assert emotion in STATES
        return {"speaker": speaker, "text": text, "emotion": emotion, "intensity": 0.6,
                "delivery": [], "step": step, "objection": objection, "pause_ms": 300, "overlap_ms": 0}

    turns = [t("seller", "Bonjour, Léa de The Herakles.", "hook"),
             t("seller", "Je vous préviens que j'utilise un assistant qui analyse l'échange.", "announcement"),
             t("prospect", "D'accord.", "announcement", arc[0])]
    for o in objections:
        turns.append(t("prospect", "Bon, franchement, ça ne m'arrange pas trop.", "hook", arc[1], o))
        turns.append(t("seller", "Je comprends tout à fait.", "hook"))
    filler = "On aide les entreprises comme la vôtre à gagner en visibilité, simplement et rapidement."
    while sum(len(x["text"].split()) for x in turns) < words * 0.9:
        turns.append(t("seller", filler, "value"))
        turns.append(t("prospect", "Hum, je vois, continuez.", "value", arc[1]))
    turns += [t("seller", "On fixe un créneau ?", "close"), t("prospect", "Voyons.", "close", arc[2]),
              t("prospect", "Au revoir.", "wrap_up", arc[2])]
    return {"seller_name": "Léa", "prospect_name": "Paul Fictif", "prospect_company": "Fictive SARL",
            "offer": "site web", "turns": turns, "outcome": outcome}


@pytest.fixture
def mocked_apis(monkeypatch, tmp_path):
    import anthropic
    import elevenlabs

    anthropic_requests, eleven_requests = [], []

    def anthropic_handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        anthropic_requests.append((dict(request.headers), body))
        dialogue = fake_dialogue_from_brief(body["messages"][0]["content"])
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(dialogue, ensure_ascii=False)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}})

    def eleven_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        eleven_requests.append(body)
        n = int(0.004 * len(body["text"]) * 16000)
        pcm = array("h", (int(2000 * math.sin(i / 5)) for i in range(n))).tobytes()
        return httpx.Response(200, headers={"request-id": "r"}, json={
            "audio_base64": base64.b64encode(pcm).decode(), "alignment": None, "normalized_alignment": None})

    real_anthropic = anthropic.AsyncAnthropic
    real_eleven = elevenlabs.AsyncElevenLabs
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: real_anthropic(
        api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(anthropic_handler))))
    monkeypatch.setattr(elevenlabs, "AsyncElevenLabs", lambda **kw: real_eleven(
        api_key="test", httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(eleven_handler))))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test")

    book = {}
    for lang in ("fr", "en"):
        book[lang] = {g: [{"voice_id": f"{lang}{g[0]}{i}", "split": s}
                          for i, s in enumerate(["train", "train", "val", "val", "test", "test"])]
                      for g in ("female", "male")}
    vf = tmp_path / "voices.json"
    vf.write_text(json.dumps(book))
    monkeypatch.setattr(voices_mod, "VOICES_FILE", vf)
    return anthropic_requests, eleven_requests


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["datagen", *argv])
    main()


def test_pilot_end_to_end(mocked_apis, monkeypatch, tmp_path, capsys):
    anthropic_requests, eleven_requests = mocked_apis
    root = tmp_path / "synthetic"
    run_cli(monkeypatch, "--root", str(root), "pilot", "--n", "6")
    out = capsys.readouterr().out

    # Dialogues : requête bêta avec repli serveur et sortie structurée.
    headers, body = anthropic_requests[0]
    assert "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert body["fallbacks"] == "default"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["model"] == "claude-opus-5-5"

    calls = sorted(p for p in (root / "calls").iterdir())
    assert len(calls) == 6
    for call in calls:
        for name in ("dialogue.json", "turns.json", "provenance.json", "call.wav", "labels.json", "replay.json"):
            assert (call / name).exists(), (call.name, name)
    assert eleven_requests and all(r["model_id"] == "eleven_v4" for r in eleven_requests)
    assert "Objection" in out and (root / "report.md").exists()

    # Relance : rien n'est redemandé (ni refacturé).
    n_a, n_e = len(anthropic_requests), len(eleven_requests)
    run_cli(monkeypatch, "--root", str(root), "pilot", "--n", "6")
    assert (len(anthropic_requests), len(eleven_requests)) == (n_a, n_e)


def test_pilot_spending_guard(mocked_apis, monkeypatch, tmp_path):
    _, eleven_requests = mocked_apis
    with pytest.raises(SystemExit, match="max-chars"):
        run_cli(monkeypatch, "--root", str(tmp_path / "s"), "pilot", "--n", "3", "--max-chars", "100")
    assert eleven_requests == []


def test_choose_library_voices_filters_custom_rates_and_language():
    from types import SimpleNamespace as V

    def v(i, **kw):
        base = dict(voice_id=f"v{i}", language="fr", verified_languages=[], rate=None, fiat_rate=None,
                    notice_period=None, use_case="conversational", cloned_by_count=i)
        base.update(kw)
        return V(**base)

    cands = [v(1), v(2, rate=2.0), v(3, language="en"), v(4, notice_period=30), v(5),
             v(6, use_case="characters_animation"), v(7, use_case="narrative_story")]
    chosen = voices_mod.choose_library_voices(cands, "fr", 3)
    assert [c.voice_id for c in chosen] == ["v5", "v1", "v7"]
    assert [voices_mod.split_for(r, 6) for r in range(6)] == ["train", "train", "val", "val", "test", "test"]


def test_voices_limit_progresses_across_runs(mocked_apis, monkeypatch, tmp_path):
    root = tmp_path / "s"
    run_cli(monkeypatch, "--root", str(root), "pilot", "--n", "3", "--skip-voices")
    for _ in range(3):
        run_cli(monkeypatch, "--root", str(root), "voices", "--limit", "1")
    done = [c.name for c in (root / "calls").iterdir() if (c / "turns.json").exists()]
    assert len(done) == 3
