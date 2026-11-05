"""Régressions des défauts trouvés par la 2e revue indépendante (moteur et session)."""

import asyncio

from app.coach import CoachEngine, LLMHint
from app.coach.providers import CoachContext
from app.session import CallSession


def collector():
    out = []

    async def send(m):
        out.append(m)
    return out, send


def hints(out):
    return [m["hint"] for m in out if m["type"] == "hint"]


async def started(provider, **start):
    out, send = collector()
    s = CallSession(send, provider=provider)
    await s.handle({"type": "start", **start})
    await s.handle({"type": "announcement_done", "ts": 0, "consent": True})
    return s, out


async def say(s, text, start, end, speaker="prospect"):
    await s.handle({"type": "transcript", "speaker": speaker, "text": text, "start": start, "end": end})


class Down:
    async def suggest(self, ctx):
        return None  # panne, quota, clé expirée


async def test_llm_outage_falls_back_to_keywords_for_objections_and_steps():
    s, out = await started(Down())
    await say(s, "Pas le temps, désolé.", 1, 2)
    await say(s, "Oui, allez-y.", 3, 4)
    await s.coach.wait_llm()
    assert "no_time" in s.tracker.objections_seen
    assert s.tracker.step.id != "hook"  # l'étape avance quand même sur les mots-clés
    assert s.provisional == {} and s.pending_turns == {}


class Repeat:
    async def suggest(self, ctx):
        if "no_budget" in ctx.objections_raised:
            return LLMHint(kind="question", text="Continue", say=None, confidence=0.9, objection=None)
        return LLMHint(kind="objection", text="Budget", say=None, confidence=0.9, objection="no_budget")


async def test_repeated_objection_does_not_reset_the_anti_repeat_window():
    s, out = await started(Repeat())
    await say(s, "C'est trop cher.", 1, 3)
    await s.coach.wait_llm()
    await say(s, "Non vraiment, trop cher.", 20, 22)
    await s.coach.wait_llm()
    await say(s, "Je vous dis, trop cher.", 40, 42)
    await s.coach.wait_llm()
    rule_cards = [h for h in hints(out) if h["kind"] == "objection" and h["source"] == "rule"]
    assert len(rule_cards) == 1  # une seule fiche dans les 90 s


class Slow:
    def __init__(self, delay, step=None, objection=None):
        self.delay, self.step, self.objection = delay, step, objection

    async def suggest(self, ctx):
        await asyncio.sleep(self.delay)
        return LLMHint(kind="closing", text="Verrouille le créneau.", say=None, confidence=0.9,
                       step=self.step, objection=self.objection)


async def test_stop_lets_in_flight_classifications_finish_before_the_summary():
    s, out = await started(Slow(0.2, step="close"))
    await say(s, "On pourrait se voir quinze minutes ?", 1, 3, speaker="user")
    await say(s, "Oui, mardi 10 h ça me va.", 4, 6)
    await s.handle({"type": "stop"})
    summary = next(m for m in out if m["type"] == "summary")["summary"]
    assert "close" in summary["steps_reached"]


class Cycle:
    def __init__(self):
        self.n = 0

    async def suggest(self, ctx):
        self.n += 1
        obj = "no_budget" if self.n == 1 else None
        return LLMHint(kind="objection" if obj else "question", text="x", say=None, confidence=0.9, objection=obj)


async def test_active_objection_is_cleared_once_handled():
    s, out = await started(Cycle())
    await say(s, "C'est trop cher.", 1, 2)
    await s.coach.wait_llm()
    assert s.tracker.active_objection is not None
    await say(s, "D'accord, je vois.", 3, 4)
    await s.coach.wait_llm()
    assert s.tracker.active_objection is None


class OneObjection:
    async def suggest(self, ctx):
        return LLMHint(kind="objection", text="Budget", say=None, confidence=0.9, objection="no_budget")


async def test_merged_reply_with_two_objections_keeps_both():
    s, out = await started(OneObjection())
    await say(s, "Honnêtement c'est trop cher.", 1, 3)
    await say(s, "Et puis on a déjà une agence.", 3.7, 5)
    await s.coach.wait_llm()
    assert set(s.tracker.objections_seen) == {"no_budget", "has_provider"}
    # La fiche « prestataire » des mots-clés reste à l'écran (avant : remplacée par une seconde
    # fiche budget), et c'est l'objection en cours (la plus récente).
    hints = [m["hint"] for m in out if m["type"] == "hint" and m["hint"]["kind"] == "objection"]
    provider_card = next(h for h in hints if h["ref"] == "has_provider")
    assert not any(h.get("replaces") == provider_card["id"] for h in hints)
    assert sum(h["ref"] == "no_budget" and not h.get("replaces") for h in hints) <= 1
    assert s.tracker.active_objection.id == "has_provider"


async def test_prospect_turn_is_not_dropped_behind_seller_classifications():
    out = []

    async def emit(h):
        out.append(h)
    seen = []

    class Rec:
        async def suggest(self, ctx):
            seen.append(ctx.recent_transcript[-1] if ctx.recent_transcript else "")
            await asyncio.sleep(0.2)
            return LLMHint(kind="question", text="x", say=None, confidence=0.9)

    engine = CoachEngine(emit, provider=Rec())
    ctx = CoachContext(language="fr", scenario_title="t", step_title="s", step_goal="g", objection=None,
                       prospect_emotion=None, metrics={}, recent_transcript=["prospect: on a une agence"])
    for i in range(8):  # le vendeur enchaîne les fragments
        engine.request_llm(ctx, float(i), replaces=None, classify_only=True)
    engine.request_llm(ctx, 9.0, replaces=None)  # tour du prospect
    assert any(t not in engine._classify_tasks for t in engine._llm_tasks)
    await engine.wait_llm()


async def test_hung_llm_is_cut_off_and_turn_falls_back():
    class Hung:
        async def suggest(self, ctx):
            await asyncio.sleep(60)

    s, out = await started(Hung())
    s.coach.LLM_TIMEOUT = 0.1
    await say(s, "Pas le temps.", 1, 2)
    await s.coach.wait_llm()
    assert "no_time" in s.tracker.objections_seen


async def test_pause_keeps_classification_but_drops_emotion_hints():
    class EmotionAndObjection:
        async def suggest(self, ctx):
            await asyncio.sleep(0.1)
            return LLMHint(kind="emotion", text="Il hésite", say=None, confidence=0.9, objection=None, step="value")

    s, out = await started(EmotionAndObjection())
    await say(s, "Euh, je sais pas.", 1, 2)
    await s.handle({"type": "pause_analysis"})
    await s.coach.wait_llm()
    assert s.tracker.step.id == "value"  # le classement a abouti
    assert not any(h["source"] == "llm" and h["kind"] == "emotion" for h in hints(out))


async def test_second_start_is_rejected():
    s, out = await started(Down())
    try:
        await s.handle({"type": "start"})
        raise AssertionError("un second start doit être refusé")
    except RuntimeError:
        pass


async def test_low_confidence_rejection_still_withdraws_the_keyword_card():
    class Unsure:
        async def suggest(self, ctx):
            return LLMHint(kind="question", text="Pas une objection", say=None, confidence=0.3, objection=None)

    s, out = await started(Unsure())
    await say(s, "Ah non, pas maintenant, on a fini le chantier, ça marche très bien.", 1, 3)
    await s.coach.wait_llm()
    rule = next(h for h in hints(out) if h["kind"] == "objection")
    assert any(h.get("replaces") == rule["id"] for h in hints(out))


async def test_out_of_order_results_do_not_overwrite_newer_reading():
    class Order:
        async def suggest(self, ctx):
            last = ctx.recent_transcript[-1]
            if "sais pas" in last:
                await asyncio.sleep(0.3)
                return LLMHint(kind="emotion", text="x", say=None, confidence=0.9,
                               prospect_state="hesitation", state_confidence=0.9)
            await asyncio.sleep(0.01)
            return LLMHint(kind="emotion", text="y", say=None, confidence=0.9,
                           prospect_state="enthusiasm", state_confidence=0.9)

    s, out = await started(Order())
    await say(s, "Euh... je sais pas trop.", 1, 2)
    await say(s, "Ah oui, top, on fait ça !", 5, 6)
    await s.coach.wait_llm()
    from app.models import Speaker
    assert s.emotions.reading(Speaker.PROSPECT, 10).state.value == "enthusiasm"


async def test_late_result_for_an_older_turn_does_not_become_the_active_objection():
    class Late:
        async def suggest(self, ctx):
            last = ctx.recent_transcript[-1]
            if "budget" in last:
                await asyncio.sleep(0.3)  # tour ancien, réponse lente
                return LLMHint(kind="objection", text="b", say=None, confidence=0.9, objection="no_budget")
            await asyncio.sleep(0.01)
            return LLMHint(kind="objection", text="p", say=None, confidence=0.9, objection="has_provider")

    s, out = await started(Late())
    await say(s, "Le budget est serré cette année.", 1, 3)
    await say(s, "Bon, et de toute façon on travaille avec une agence.", 8, 10)
    await s.coach.wait_llm()
    assert set(s.tracker.objections_seen) == {"no_budget", "has_provider"}
    assert s.tracker.active_objection.id == "has_provider"  # avant : remplacée par l'ancienne (budget)


async def test_overtime_on_the_last_step_says_conclude_not_move_on():
    out = []

    async def emit(h):
        out.append(h)

    from app.scenario import load_scenario
    steps = load_scenario("cold_call_fr").steps
    engine = CoachEngine(emit)
    await engine.on_overtime(steps[-1], 100.0, last=True)
    await engine.on_overtime(steps[1], 120.0)
    assert "passe à la suite" not in out[0].text and "conclus" in out[0].text
    assert "passe à la suite" in out[1].text
