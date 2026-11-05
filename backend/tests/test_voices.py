import asyncio
import base64
import json
import math
from array import array
from pathlib import Path

import httpx
import pytest

from datagen.dialogue import Dialogue, Turn
from datagen.plan import CallSpec
from datagen.voices import (
    Voice, VoiceBook, assign_voices, load_turn_audios, render_text, stability, synthesize_call,
    words_from_alignment,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dialogue_fr.json"


def turn(**kw) -> Turn:
    base = dict(speaker="prospect", text="Je sais pas trop.", emotion="neutral", intensity=0.3,
                delivery=[], step="hook", objection=None, pause_ms=0, overlap_ms=0)
    base.update(kw)
    return Turn(**base)


def book() -> VoiceBook:
    def pool(prefix):
        return [Voice(voice_id=f"{prefix}{i}", split=s) for i, s in enumerate(["train", "train", "val", "test"])]
    return VoiceBook(fr={"female": pool("ff"), "male": pool("fm")}, en={"female": pool("ef"), "male": pool("em")})


def test_render_text_uses_delivery_then_emotion_fallback():
    assert render_text(turn(delivery=["hesitant", "sighs"])) == "[hesitant] [sighs] Je sais pas trop."
    assert render_text(turn(emotion="annoyance", intensity=0.8)) == "[annoyed] Je sais pas trop."
    assert render_text(turn(emotion="annoyance", intensity=0.3)) == "Je sais pas trop."


def test_stability_lower_for_intense_emotion():
    assert stability(turn()) == 0.6
    assert stability(turn(emotion="annoyance", intensity=1.0)) == 0.3
    assert 0.3 < stability(turn(emotion="interest", intensity=0.5)) < 0.6


def test_words_from_alignment_strips_tags():
    text = "[sighs] Bon, ok."
    chars = list(text)
    starts = [i * 0.1 for i in range(len(chars))]
    ends = [s + 0.1 for s in starts]
    assert [w for w, _, _ in words_from_alignment(chars, starts, ends)] == ["Bon,", "ok."]


def test_voice_assignment_respects_split_and_distinct_speakers():
    spec = CallSpec(call_id="fr-0001", language="fr", sector="x", prospect_role="owner",
                    prospect_personality="rushed", prospect_gender="male", seller_level="junior",
                    seller_gender="female", objections=[], arc=["neutral"] * 3, outcome="refused",
                    target_minutes=3, split="test", seed=5)
    v = assign_voices(spec, book())
    assert v["seller"].split == v["prospect"].split == "test"
    # Une seule voix féminine de test : le prospect ne peut pas reprendre celle du vendeur.
    with pytest.raises(ValueError):
        book().pick("fr", "female", "test", 1, exclude="ff3")


def test_synthesize_call_against_mock_elevenlabs(tmp_path):
    from elevenlabs import AsyncElevenLabs

    dialogue = Dialogue.model_validate_json(FIXTURE.read_text("utf-8"))
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append((request.url.path, dict(request.url.params), body))
        text = body["text"]
        n = int(0.05 * len(text) * 16000)
        pcm = array("h", (int(3000 * math.sin(i / 7)) for i in range(n))).tobytes()
        chars = list(text)
        starts = [i * 0.05 for i in range(len(chars))]
        return httpx.Response(200, headers={"request-id": f"req-{len(bodies)}", "character-cost": str(len(text))},
                              json={"audio_base64": base64.b64encode(pcm).decode(),
                                    "alignment": {"characters": chars, "character_start_times_seconds": starts,
                                                  "character_end_times_seconds": [s + 0.05 for s in starts]},
                                    "normalized_alignment": None})

    client = AsyncElevenLabs(api_key="test", httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    spec = CallSpec(call_id=dialogue.call_id, language="fr", sector="x", prospect_role="owner",
                    prospect_personality="rushed", prospect_gender="male", seller_level="senior",
                    seller_gender="female", objections=["no_time", "has_provider"],
                    arc=["annoyance", "hesitation", "enthusiasm"], outcome="meeting_booked",
                    target_minutes=1, split="train", seed=9)
    voices = assign_voices(spec, book())
    asyncio.run(synthesize_call(client, tmp_path, dialogue, spec, voices, "eleven_v4", asyncio.Semaphore(2)))

    assert len(bodies) == len(dialogue.turns)
    path, params, first = bodies[0]
    assert path == f"/v1/text-to-speech/{voices['seller'].voice_id}/with-timestamps"
    assert params["output_format"] == "pcm_16000"
    assert first["model_id"] == "eleven_v4" and first["language_code"] == "fr"
    assert "previous_text" not in first  # jamais de null explicite
    meta = json.loads((tmp_path / "turns.json").read_text())
    assert meta[0]["request_id"] and meta[2]["words"][0][0]
    assert json.loads((tmp_path / "provenance.json").read_text())["voice_provider"] == "elevenlabs"
    audios = load_turn_audios(tmp_path)
    assert audios and len(audios) == len(dialogue.turns) and audios[0].sample_rate == 16000
