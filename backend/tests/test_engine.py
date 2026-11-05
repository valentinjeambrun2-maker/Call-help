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
    await engine._llm_task
    assert [h.source for h in out] == ["rule", "llm"]
    assert out[1].replaces == rule.id
    assert engine.hints_given == 1


async def test_new_request_cancels_stale_llm_generation():
    out, emit = collector()
    engine = CoachEngine(emit, provider=FakeProvider(delay=0.2))
    engine.request_llm(CTX, 1, replaces=None)
    first = engine._llm_task
    await asyncio.sleep(0)
    engine.request_llm(CTX, 2, replaces=None)
    await engine._llm_task
    assert first.cancelled()
    assert len(out) == 1 and out[0].ts == 2


async def test_low_confidence_llm_stays_silent():
    out, emit = collector()
    engine = CoachEngine(emit, provider=FakeProvider(confidence=0.3))
    engine.request_llm(CTX, 1, replaces=None)
    await engine._llm_task
    assert out == []


async def test_monologue_alert():
    out, emit = collector()
    engine = CoachEngine(emit)
    await engine.on_metrics(Metrics(elapsed=60, current_monologue=50), 60)
    assert any(h.kind == "pace" and "50 s" in h.text for h in out)
