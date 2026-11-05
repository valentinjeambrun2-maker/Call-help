"""Régressions des défauts trouvés par la 3e revue (captation d'un vrai appel)."""

from app.coach import LLMHint
from app.coach.providers import CoachContext, hint_schema
from app.session import CallSession


def collector():
    out = []

    async def send(m):
        out.append(m)
    return out, send


async def say(s, text, start, end, speaker="prospect"):
    await s.handle({"type": "transcript", "speaker": speaker, "text": text, "start": start, "end": end})


async def test_consent_reply_transcribed_after_the_click_does_not_skip_the_hook():
    """Le « d'accord » du prospect arrive ~1 s après la fin de sa phrase (transcription en
    direct), souvent après le clic « Il est d'accord » : il répond au message, il ne clôt pas
    l'accroche que le vendeur n'a pas encore dite."""
    out, send = collector()
    s = CallSession(send)  # mots-clés seuls (aussi le repli quand la clé Anthropic est refusée)
    await s.handle({"type": "start"})
    await s.handle({"type": "announcement_done", "ts": 5.46, "consent": True})
    await say(s, "Oui, d'accord, allez-y.", 4.23, 5.24)  # dit avant le clic, reçu après
    assert s.tracker.step.id == "hook"
    assert any("allez-y" in line for line in s.full_transcript)  # gardé pour le bilan
    await say(s, "D'accord, je vous écoute.", 9.0, 10.0)  # réponse à l'accroche
    assert s.tracker.step.id != "hook"


class EmotionHint:
    def __init__(self):
        self.contexts = []

    async def suggest(self, ctx):
        self.contexts.append(ctx)
        return LLMHint(kind="emotion", text="Il semble agacé : ralentis.", say=None, confidence=0.9)


async def test_refusal_blocks_emotion_hints_even_if_the_model_returns_one():
    provider = EmotionHint()
    out, send = collector()
    s = CallSession(send, provider=provider)
    await s.handle({"type": "start"})
    await s.handle({"type": "announcement_done", "ts": 2, "consent": False})
    await say(s, "Bon, allez-y, mais faites vite.", 3, 5)
    await s.coach.wait_llm()
    assert provider.contexts and not provider.contexts[-1].emotion_allowed
    assert not [m for m in out if m["type"] == "hint" and m["hint"]["kind"] == "emotion"]


def test_without_consent_the_model_is_not_even_asked_for_the_prospect_state():
    ctx = CoachContext(language="fr", scenario_title="t", step_title="s", step_goal="g", objection=None,
                       prospect_emotion=None, metrics={}, recent_transcript=[],
                       steps=[{"id": "hook", "title": "Accroche", "goal": "g"}])
    refused = hint_schema(ctx.model_copy(update={"emotion_allowed": False}))
    assert "prospect_state" not in refused["properties"] and "state_confidence" not in refused["properties"]
    assert "emotion" not in refused["properties"]["kind"]["enum"]
    assert set(refused["required"]) == set(refused["properties"])
    allowed = hint_schema(ctx.model_copy(update={"emotion_allowed": True}))
    assert "prospect_state" in allowed["properties"] and "emotion" in allowed["properties"]["kind"]["enum"]


async def test_summary_refused_with_403_says_why(monkeypatch):
    import anthropic
    import httpx2

    from app.coach.summary import ClaudeSummary

    def forbid(request):
        return httpx2.Response(403, json={"type": "error", "error": {"type": "permission_error",
                                                                      "message": "model not allowed"}})

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    summary = ClaudeSummary()
    summary.client = anthropic.AsyncAnthropic(api_key="test", max_retries=0,
                                              http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(forbid)))
    out, send = collector()
    s = CallSession(send, summary_provider=summary)
    await s.handle({"type": "start"})
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    await say(s, "Bonjour.", 1, 2, speaker="user")
    await say(s, "Bonjour, oui ?", 3, 4)
    await s.handle({"type": "stop"})
    await s.summary_task
    ai = next(m for m in out if m["type"] == "ai_summary")
    assert ai["error"] == "Bilan IA indisponible (accès Anthropic refusé)."
