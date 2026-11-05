"""Régressions des défauts trouvés par la revue indépendante (novembre 2026)."""

import asyncio

from fastapi.testclient import TestClient

from app.coach import CoachEngine, LLMHint
from app.main import app
from app.metrics import is_question
from app.models import EmotionReading, EmotionState as E, Hint, Metrics, Speaker
from app.scenario import contains_keyword
from app.session import CallSession

HESITANT = {"interest": 0.1, "hesitation": 0.95, "annoyance": 0.2, "boredom": 0.1, "enthusiasm": 0.0}
NEUTRAL = {"interest": 0.2, "hesitation": 0.2, "annoyance": 0.1, "boredom": 0.1, "enthusiasm": 0.2}


def collect():
    out = []

    async def send(msg):
        out.append(msg)
    return out, send


async def started(provider=None, **start):
    out, send = collect()
    s = CallSession(send, provider=provider)
    await s.handle({"type": "start", **start})
    return s, out


def hints(out):
    return [m["hint"] for m in out if m["type"] == "hint"]


async def test_prospect_ok_during_pending_notice_does_not_advance_or_hide_it():
    s, out = await started()
    await s.handle({"type": "transcript", "speaker": "user", "text": "Avant de commencer, je vous préviens…",
                    "start": 0, "end": 31})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "D'accord, allez-y.",
                    "start": 32, "end": 33})
    assert [h["kind"] for h in hints(out)] == ["announcement"]  # ni étape ni « dure trop »
    assert s.tracker.step.id == "hook" and out[-1]["announcement_pending"] is True
    await s.handle({"type": "announcement_done", "ts": 34, "consent": True})
    assert hints(out)[-1]["ref"] == "hook"


async def test_pause_cancels_in_flight_llm_hint():
    class Slow:
        async def suggest(self, ctx):
            await asyncio.sleep(0.2)
            return LLMHint(kind="emotion", text="Il semble hésiter", say=None, confidence=0.9)

    s, out = await started(provider=Slow(), scenario_id="cold_call_fr")
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "Hum.", "start": 1, "end": 2})
    await s.handle({"type": "pause_analysis"})
    await asyncio.sleep(0.3)
    assert not any(h["source"] == "llm" for h in hints(out))


async def test_emotion_hint_repeats_after_return_to_neutral_and_after_rate_limit():
    out = []

    async def emit(h):
        out.append(h)
    engine = CoachEngine(emit)
    hes = EmotionReading(speaker=Speaker.PROSPECT, state=E.HESITATION, confidence=0.8, label="sûr", calibrated=True)
    neutral = EmotionReading(speaker=Speaker.PROSPECT, state=None, calibrated=True)
    await engine.on_emotion(hes, 40)
    await engine.on_emotion(neutral, 60)
    await engine.on_emotion(hes, 120)
    assert len([h for h in out if h.kind == "emotion"]) == 2

    out.clear()
    engine = CoachEngine(emit)
    for i in range(6):  # remplit la fenêtre de 20 s
        await engine.on_metrics(Metrics(elapsed=60 + i, current_monologue=50 + i), 50 + i * 0.1)
        engine.limiter.last_by_key.clear()
    ann = hes.model_copy(update={"state": E.ANNOYANCE})
    assert await engine.on_emotion(ann, 51) is None      # refusé par la limite
    assert await engine.on_emotion(ann, 80) is not None  # proposé à nouveau ensuite


async def test_compliance_trace_when_cut_before_any_sample():
    s, out = await started()
    await s.handle({"type": "pause_analysis"})
    await s.handle({"type": "announcement_done", "ts": 10, "consent": True})
    for ts in range(10, 50, 5):
        await s.handle({"type": "voice", "speaker": "prospect", "ts": ts, "scores": HESITANT})
    assert s.summary().emotion_analysis_used is False


def test_keyword_after_french_elision():
    assert contains_keyword("C'est justement ce qu'on cherche.", "on cherche")
    assert contains_keyword("Je crois qu'on a déjà ce qu'il faut.", "on a déjà")
    assert not contains_keyword("Je lis un book", "ok")


def test_question_detection_uses_whole_words():
    for t in ("Quelqu'un de chez vous m'a recommandé.", "Commentaire rapide.", "However, we can.", "Ou alors jeudi."):
        assert not is_question(t), t
    for t in ("Comment gérez-vous ça", "Est-ce que vous avez un site", "Do you have a minute", "Ça vous va ?"):
        assert is_question(t), t


def test_invalid_frames_do_not_end_the_call(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json(); ws.receive_json()
        ws.send_text("{oops")
        assert ws.receive_json()["type"] == "error"
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "summary"


async def test_llm_told_not_to_use_emotions_without_consent():
    seen = []

    class Spy:
        async def suggest(self, ctx):
            seen.append(ctx)
            return None

    s, out = await started(provider=Spy())
    await s.handle({"type": "announcement_done", "ts": 0, "consent": False})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "Hum.", "start": 1, "end": 2})
    await asyncio.sleep(0)
    await s.coach.close()
    assert seen and seen[-1].emotion_allowed is False and seen[-1].prospect_emotion is None


async def test_feedback_on_refined_hint_counts_once():
    class Refine:
        async def suggest(self, ctx):
            return LLMHint(kind="objection", text="Plus fin", say=None, confidence=0.9)

    s, out = await started(provider=Refine())
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "J'ai pas le temps.", "start": 1, "end": 2})
    await s.coach.wait_llm()
    rule = next(h for h in hints(out) if h["kind"] == "objection" and h["source"] == "rule")
    llm = next(h for h in hints(out) if h["source"] == "llm")
    await s.handle({"type": "feedback", "hint_id": rule["id"], "useful": True})
    await s.handle({"type": "feedback", "hint_id": llm["id"], "useful": True})
    summary = s.summary()
    assert summary.hints_useful == 1 and summary.hints_useful <= summary.hints_given


async def test_llm_classification_catches_objection_missed_by_keywords_and_advances_step():
    class Classifier:
        async def suggest(self, ctx):
            assert {o["id"] for o in ctx.objections} >= {"has_provider"} and ctx.current_step_id == "hook"
            return LLMHint(kind="question", text="Demande ce que l'agence ne couvre pas.", say=None,
                           confidence=0.8, objection="has_provider", step="value")

    s, out = await started(provider=Classifier())
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await s.handle({"type": "transcript", "speaker": "prospect",
                    "text": "Bon, on bosse déjà avec une agence pour le site.", "start": 1, "end": 3})
    await s.coach.wait_llm()
    objection = [h for h in hints(out) if h["kind"] == "objection"]
    assert objection and objection[-1]["ref"] == "has_provider" and objection[-1]["source"] == "llm"
    assert s.tracker.step.id == "value" and "has_provider" in s.tracker.objections_seen
    assert any(h["kind"] == "step" and h["ref"] == "value" for h in hints(out))


async def test_llm_other_objection_does_not_create_fake_ref():
    class Other:
        async def suggest(self, ctx):
            return LLMHint(kind="objection", text="Rassure sur la confidentialité.", say=None,
                           confidence=0.8, objection="other", step="hook")

    s, out = await started(provider=Other())
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "Et mes données ?", "start": 1, "end": 2})
    await s.coach.wait_llm()
    llm = [h for h in hints(out) if h["source"] == "llm"]
    assert llm and llm[-1]["ref"] is None and s.tracker.objections_seen == []
