import asyncio

from app.coach import CoachContext, CoachEngine, LLMHint, RateLimiter
from app.models import Metrics
from app.scenario import load_scenario

CTX = CoachContext(language="fr", scenario_title="t", step_title="s", step_goal="g",
                   objection=None, prospect_emotion=None, metrics={}, recent_transcript=[])


class FakeProvider:
    def __init__(self, delay=0.0, confidence=0.9):
        self.delay, self.confidence, self.calls = delay, confidence, 0

    async def suggest(self, ctx):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return LLMHint(kind="objection", text="Conseil fin", say="Phrase", confidence=self.confidence)


def collector():
    out = []

    async def emit(h):
        out.append(h)
    return out, emit


def test_rate_limiter_caps_but_lets_urgent_through():
    rl = RateLimiter(max_hints=2, window=20, cooldown=0)
    assert rl.allow("a", 0, 1) and rl.allow("b", 1, 1)
    assert not rl.allow("c", 2, 1)
    assert rl.allow("d", 3, 3)  # urgent
    assert rl.allow("e", 25, 1)  # fenêtre écoulée


def test_rate_limiter_cooldown_per_key():
    rl = RateLimiter(cooldown=15)
    assert rl.allow("x", 0, 3)
    assert not rl.allow("x", 10, 3)
    assert rl.allow("x", 16, 3)


async def test_objection_rule_fills_variables():
    out, emit = collector()
    engine = CoachEngine(emit, context_vars={"topic": "le web"})
    objection = next(o for o in load_scenario("cold_call_fr").objections if o.id == "send_email")
    await engine.on_objection(objection, 10)
    assert out[0].priority == 3 and "le web" in out[0].say


async def test_llm_hint_replaces_rule_hint():
    out, emit = collector()
    engine = CoachEngine(emit, provider=FakeProvider())
    objection = load_scenario("cold_call_fr").objections[0]
    rule = await engine.on_objection(objection, 10)
    engine.request_llm(CTX, 10, replaces=rule)
    await engine.wait_llm()
    assert [h.source for h in out] == ["rule", "llm"]
    assert out[1].replaces == rule.id
    assert engine.hints_given == 1


async def test_stale_llm_result_still_classifies_but_generic_hint_is_dropped():
    """En direct, le prospect reparle avant le retour du LLM : l'analyse du tour
    précédent n'est plus annulée (son classement compte), seul son conseil générique
    dépassé n'est pas affiché."""
    out, emit = collector()
    classified = []

    async def on_classified(result, ts, prospect_turn=True):
        classified.append(ts)

    class Generic(FakeProvider):
        async def suggest(self, ctx):
            await asyncio.sleep(self.delay)
            return LLMHint(kind="question", text="Conseil", say=None, confidence=0.9)

    engine = CoachEngine(emit, provider=Generic(delay=0.05), on_classified=on_classified)
    engine.request_llm(CTX, 1, replaces=None)
    engine.request_llm(CTX, 2, replaces=None)
    await engine.wait_llm()
    assert sorted(classified) == [1, 2]       # les deux tours sont classés
    assert [h.ts for h in out] == [2]         # seul le conseil du dernier tour est affiché


async def test_stale_llm_objection_is_still_shown():
    out, emit = collector()
    engine = CoachEngine(emit, provider=FakeProvider(delay=0.05), objection_ids={"no_time"})

    class Obj(FakeProvider):
        async def suggest(self, ctx):
            await asyncio.sleep(self.delay)
            return LLMHint(kind="objection", text="Réponds au manque de temps", say=None,
                           confidence=0.9, objection="no_time")

    engine.provider = Obj(delay=0.05)
    engine.request_llm(CTX, 1, replaces=None)
    engine.request_llm(CTX, 2, replaces=None)
    await engine.wait_llm()
    assert any(h.kind == "objection" and h.ts == 1 for h in out)


async def test_low_confidence_llm_stays_silent():
    out, emit = collector()
    engine = CoachEngine(emit, provider=FakeProvider(confidence=0.3))
    engine.request_llm(CTX, 1, replaces=None)
    await engine.wait_llm()
    assert out == []


async def test_monologue_alert():
    out, emit = collector()
    engine = CoachEngine(emit)
    await engine.on_metrics(Metrics(elapsed=60, current_monologue=50), 60)
    assert any(h.kind == "pace" and "50 s" in h.text for h in out)
