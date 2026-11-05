import asyncio

from app.coach import LLMHint
from app.coach.summary import AiSummary, FollowUpEmail
from app.session import CallSession


def collector():
    out = []

    async def send(m):
        out.append(m)
    return out, send


class Reader:
    """Classe le dernier tour du prospect comme hésitant (lecture dans les mots)."""

    def __init__(self):
        self.contexts = []

    async def suggest(self, ctx):
        self.contexts.append(ctx)
        allowed = ctx.emotion_allowed
        return LLMHint(kind="question", text="Rassure-le.", say=None, confidence=0.9,
                       prospect_state="hesitation" if allowed else None, state_confidence=0.8 if allowed else 0)


async def run(consent: bool):
    out, send = collector()
    s = CallSession(send, provider=Reader())
    await s.handle({"type": "start"})
    await s.handle({"type": "announcement_done", "ts": 0, "consent": consent})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "Euh... je sais pas trop.", "start": 1, "end": 3})
    await s.coach.wait_llm()
    return s, out


async def test_text_reading_feeds_prospect_tile_with_consent():
    s, out = await run(True)
    state = [m for m in out if m["type"] == "state"][-1]
    prospect = next(e for e in state["emotions"] if e["speaker"] == "prospect")
    assert prospect["state"] == "hesitation" and prospect["calibrated"]
    assert s.summary().emotion_analysis_used is True


async def test_no_text_reading_without_consent():
    s, out = await run(False)
    state = [m for m in out if m["type"] == "state"][-1]
    assert all(e["state"] is None for e in state["emotions"])
    assert s.summary().emotion_analysis_used is False


class FakeSummary:
    def __init__(self):
        self.inputs = []

    async def summarize(self, data):
        self.inputs.append(data)
        return AiSummary(summary="Appel court.", outcome="rappel", next_step="Rappeler jeudi.",
                         objections=[], strengths=["Écoute"], improvements=["Qualifier le budget"],
                         follow_up_email=FollowUpEmail(subject="Suite à notre appel", body="Bonjour..."))


async def test_ai_summary_follows_the_stats_summary_and_has_no_emotions():
    out, send = collector()
    fake = FakeSummary()
    s = CallSession(send, summary_provider=fake)
    await s.handle({"type": "start", "context": {"company": "The Herakles"}})
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await s.handle({"type": "transcript", "speaker": "user", "text": "Bonjour, Léa de The Herakles.", "start": 0, "end": 2})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "Pas le temps, rappelez jeudi.", "start": 3, "end": 5})
    await s.handle({"type": "stop"})
    kinds = [m["type"] for m in out]
    assert kinds.index("summary") < kinds.index("ai_summary_pending") < kinds.index("ai_summary")
    data = fake.inputs[0]
    assert data.transcript[0].startswith("[00:00] vendeur") and data.seller_company == "The Herakles"
    assert "Pas le temps" in data.objections_raised
    ai = next(m for m in out if m["type"] == "ai_summary")
    assert ai["summary"]["follow_up_email"]["subject"] and ai["error"] is None


async def test_stt_fragments_of_one_reply_are_merged_for_the_llm():
    seen = []

    class Spy:
        async def suggest(self, ctx):
            seen.append(list(ctx.recent_transcript))
            return None

    out, send = collector()
    s = CallSession(send, provider=Spy())
    await s.handle({"type": "start"})
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "On a explosé le budget.", "start": 1, "end": 3})
    await s.handle({"type": "transcript", "speaker": "prospect", "text": "Il reste plus rien.", "start": 3.7, "end": 5})
    await s.handle({"type": "transcript", "speaker": "user", "text": "Je comprends.", "start": 6, "end": 7})
    await s.coach.wait_llm()
    assert seen[1][-1] == "prospect: On a explosé le budget. Il reste plus rien."
    assert seen[2][-2:] == ["prospect: On a explosé le budget. Il reste plus rien.", "user: Je comprends."]
