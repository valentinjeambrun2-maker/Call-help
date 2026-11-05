"""Régressions des défauts du générateur trouvés par la revue indépendante."""

import asyncio
import base64
import json
import math
from array import array
from pathlib import Path

import httpx
import pytest

from datagen.assemble import timeline, write_call
from datagen.dialogue import Dialogue, parse_response_text, validate_dialogue
from datagen.evaluate import aggregate, evaluate_dir, run_call
from datagen.plan import CallSpec, build_plan
from datagen.voices import (
    Voice, VoiceBook, assign_voices, audio_complete, load_turn_audios, split_for, synthesize_call, tts_seed,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dialogue_fr.json"


def dialogue() -> Dialogue:
    return Dialogue.model_validate_json(FIXTURE.read_text("utf-8"))


def spec(**kw) -> CallSpec:
    base = dict(call_id="fr-9999", language="fr", sector="x", prospect_role="owner",
                prospect_personality="rushed", prospect_gender="male", seller_level="senior",
                seller_gender="female", objections=["no_time", "has_provider"],
                arc=["annoyance", "hesitation", "enthusiasm"], outcome="meeting_booked",
                target_minutes=0.9, split="train", seed=1)
    base.update(kw)
    return CallSpec(**base)


def book() -> VoiceBook:
    def pool(p):
        return [Voice(voice_id=f"{p}{i}", split=s) for i, s in enumerate(["train", "train", "val", "val", "test", "test"])]
    return VoiceBook(fr={"female": pool("ff"), "male": pool("fm")}, en={"female": pool("ef"), "male": pool("em")})


def eleven_client(fail_on: set[int] | None = None, calls: list | None = None):
    from elevenlabs import AsyncElevenLabs

    fail_on = fail_on if fail_on is not None else set()
    calls = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["text"])
        if len(calls) - 1 in fail_on:
            return httpx.Response(422, json={"detail": {"message": "boom"}})
        pcm = array("h", (int(2000 * math.sin(i / 5)) for i in range(1600))).tobytes()
        return httpx.Response(200, json={"audio_base64": base64.b64encode(pcm).decode(),
                                         "alignment": None, "normalized_alignment": None})
    return AsyncElevenLabs(api_key="t", httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))), calls


def test_interrupted_synthesis_resumes_without_losing_audio(tmp_path):
    d, sp = dialogue(), spec()
    voices = assign_voices(sp, book())
    client, calls = eleven_client(fail_on={5})
    with pytest.raises(Exception):
        asyncio.run(synthesize_call(client, tmp_path, d, sp, voices, "eleven_v4", asyncio.Semaphore(1)))
    assert not (tmp_path / "turns.json").exists() and not audio_complete(tmp_path, d)
    first = len(calls)
    client, calls2 = eleven_client(calls=calls)
    asyncio.run(synthesize_call(client, tmp_path, d, sp, voices, "eleven_v4", asyncio.Semaphore(1)))
    meta = json.loads((tmp_path / "turns.json").read_text())
    assert all(m is not None for m in meta) and audio_complete(tmp_path, d)
    assert len(calls2) - first == 1  # seul le tour en échec est redemandé (et refacturé)
    assert load_turn_audios(tmp_path, d) is not None


def test_rewritten_dialogue_invalidates_old_audio(tmp_path):
    d, sp = dialogue(), spec()
    client, _ = eleven_client()
    asyncio.run(synthesize_call(client, tmp_path, d, sp, assign_voices(sp, book()), "eleven_v4", asyncio.Semaphore(2)))
    turns = list(d.turns)
    turns[3] = turns[3].model_copy(update={"text": "Une toute autre réplique."})
    changed = d.model_copy(update={"turns": turns})
    assert not audio_complete(tmp_path, changed) and load_turn_audios(tmp_path, changed) is None


def test_tts_seed_stays_in_documented_range():
    for s in build_plan(5, seed=100) + build_plan(5, seed=43):
        for i in (0, 199):
            assert 0 <= tts_seed(s, i) < 2**32


def test_split_needs_two_voices_per_split():
    assert [split_for(r, 7) for r in range(7)] == ["train", "val", "test"] * 2 + ["train"]
    with pytest.raises(ValueError):
        split_for(0, 5)


def test_f1_is_zero_not_missing_on_total_failure():
    per_call = {"fr-1": {"tp": {}, "fp": {"no_budget": 2}, "fn": {"no_budget": 3}, "step_correct": 0,
                         "step_total": 0, "hints": 0, "minutes": 1}}
    r = aggregate(per_call)
    assert r["objections"]["no_budget"]["f1"] == 0.0 and r["objections_overall"]["f1"] == 0.0
    assert r["objections"]["no_time"]["f1"] is None  # aucune donnée : reste « — »


def test_step_accuracy_does_not_depend_on_rounding(tmp_path):
    scores = []
    for k, offset in enumerate((0, 20, 40)):
        d = dialogue()
        turns = list(d.turns)
        turns[0] = turns[0].model_copy(update={"pause_ms": 300 + offset})
        call = tmp_path / str(k) / "fr-9999"
        write_call(call, d.model_copy(update={"turns": turns}), None)
        scores.append(evaluate_dir(call.parent)["step_accuracy"])
    assert len(set(scores)) == 1


def test_same_speaker_never_overlaps_itself():
    d = dialogue()
    turns = list(d.turns)
    # vendeur long, prospect qui coupe, vendeur qui recoupe
    turns[5] = turns[5].model_copy(update={"speaker": "seller"})
    turns[6] = turns[6].model_copy(update={"speaker": "prospect", "overlap_ms": 900})
    turns[7] = turns[7].model_copy(update={"speaker": "seller", "overlap_ms": 300})
    d = d.model_copy(update={"turns": turns})
    durations = [10.0 if i == 5 else 0.6 for i in range(len(turns))]
    spans = timeline(d, durations)
    assert spans[7][0] >= spans[5][1]


def test_validation_requires_seller_notice():
    d = dialogue()
    turns = list(d.turns)
    turns[1] = turns[1].model_copy(update={"step": "hook"})  # seule la réaction du prospect reste « announcement »
    issues = validate_dialogue(spec(), d.model_copy(update={"turns": turns}))
    assert any("message d'information" in i for i in issues)


def test_out_of_range_numbers_are_clamped_not_rejected():
    data = json.loads(FIXTURE.read_text("utf-8"))
    data.pop("call_id")
    data["turns"][4]["pause_ms"] = 4500
    data["turns"][8]["overlap_ms"] = 1800
    data["turns"][2]["intensity"] = 1.3
    data["turns"].insert(3, {**data["turns"][3], "text": "  "})
    d = parse_response_text(spec(), json.dumps(data))
    assert d.turns[4].pause_ms == 4000 and d.turns[8].overlap_ms == 1500 and d.turns[2].intensity == 1.0
    assert len(d.turns) == len(data["turns"]) - 1


def test_english_calls_replay_in_english(tmp_path):
    d = dialogue()
    turns = list(d.turns)
    turns[2] = turns[2].model_copy(update={"text": "Sorry, I'm busy, call me back later."})
    write_call(tmp_path, d.model_copy(update={"call_id": "en-0001", "turns": turns}), None, language="en")
    replay = json.loads((tmp_path / "replay.json").read_text())
    assert replay["language"] == "en"
    hints, _ = asyncio.run(run_call(replay))
    objection = next(h for h in hints if h["kind"] == "objection")
    assert objection["say"].startswith("I understand")


def _pcm_json(n=800):
    pcm = array("h", (int(2000 * math.sin(i / 5)) for i in range(n))).tobytes()
    return {"audio_base64": base64.b64encode(pcm).decode(), "alignment": None, "normalized_alignment": None}


def test_retries_429_and_network_errors_then_falls_back_without_timestamps(tmp_path, monkeypatch):
    import datagen.voices as vm
    from elevenlabs import AsyncElevenLabs

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(vm.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(vm, "_timestamps_supported", True)
    state = {"n": 0}
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        paths.append(request.url.path)
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after-ms": "1500"}, json={"detail": {"code": "rate_limit_exceeded"}})
        if state["n"] == 2:
            raise httpx.ConnectError("reset")
        if state["n"] == 3:
            return httpx.Response(422, json={"detail": {"message": "with-timestamps not supported for eleven_v4"}})
        if request.url.path.endswith("with-timestamps"):
            return httpx.Response(200, json=_pcm_json())
        return httpx.Response(200, content=b"\x01\x00" * 800, headers={"request-id": "plain"})

    client = AsyncElevenLabs(api_key="t", httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    pcm, words, headers = asyncio.run(vm._synthesize_turn(client, "v1", {"text": "Bonjour", "model_id": "eleven_v4"}))
    assert sleeps[0] == 1.5  # retry-after-ms respecté
    assert len(sleeps) == 2  # 429 puis erreur réseau ; le 422 bascule sans attendre
    assert paths[-1].endswith("/v1/text-to-speech/v1") and words is None and len(pcm) == 1600
    assert vm._timestamps_supported is False
