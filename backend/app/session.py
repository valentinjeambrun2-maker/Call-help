"""Orchestration d'un appel : reçoit les événements, renvoie état et conseils."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable

from pydantic import TypeAdapter

from .coach import CoachContext, CoachEngine, HintProvider
from .emotion import EmotionFusion
from .metrics import ConversationMetrics
from .models import (
    AnnouncementDoneMsg, CallSummary, ClientMsg, FaceMsg, FeedbackMsg, Hint, HintMsg,
    PauseAnalysisMsg, Speaker, StartMsg, StateMsg, StopMsg, SummaryMsg, TranscriptMsg, VoiceMsg,
)
from .scenario import ScenarioTracker, load_scenario

Send = Callable[[dict], Awaitable[None]]

_client_msg = TypeAdapter(ClientMsg)


def parse_client_msg(data: dict) -> ClientMsg:
    return _client_msg.validate_python(data)


class CallSession:
    def __init__(self, send: Send, provider: HintProvider | None = None,
                 context_vars: dict[str, str] | None = None) -> None:
        self.send = send
        self.provider = provider
        self.context_vars = context_vars or {}
        self.started = False
        self.now = 0.0
        self.feedback: dict[str, bool] = {}
        self.transcript: deque[str] = deque(maxlen=12)
        self.announcement_at: float | None = None

    async def _emit_hint(self, hint: Hint) -> None:
        await self.send(HintMsg(hint=hint).model_dump(mode="json"))

    async def handle(self, data: dict) -> bool:
        """Traite un message. Retourne False quand l'appel est terminé."""
        msg = parse_client_msg(data)
        if isinstance(msg, StartMsg):
            await self._start(msg)
            return True
        if not self.started:
            raise RuntimeError("Le premier message doit être de type « start »")

        if isinstance(msg, TranscriptMsg):
            await self._on_transcript(msg)
        elif isinstance(msg, VoiceMsg):
            if msg.speaker is Speaker.PROSPECT or self.self_analysis:
                self.emotions.add_voice(msg.speaker, msg.scores, msg.ts)
            await self._on_emotion_update(msg.ts)
        elif isinstance(msg, FaceMsg):
            if msg.speaker is Speaker.PROSPECT or self.self_analysis:
                self.emotions.add_face(msg)
            await self._on_emotion_update(msg.ts)
        elif isinstance(msg, FeedbackMsg):
            self.feedback[msg.hint_id] = msg.useful
        elif isinstance(msg, PauseAnalysisMsg):
            self.emotions.paused = True
            # Un conseil IA en cours a pu être construit avec l'émotion du prospect.
            self.coach.cancel_llm()
            await self._send_state()
        elif isinstance(msg, AnnouncementDoneMsg):
            await self._on_announcement_done(msg.ts)
        elif isinstance(msg, StopMsg):
            await self.close()
            await self.send(SummaryMsg(summary=self.summary()).model_dump(mode="json"))
            return False
        return True

    async def _start(self, msg: StartMsg) -> None:
        self.scenario = load_scenario(msg.scenario_id)
        self.tracker = ScenarioTracker(self.scenario)
        self.metrics = ConversationMetrics()
        self.emotions = EmotionFusion()
        self.self_analysis = msg.self_analysis
        self.language = msg.language
        self.context_vars = {**self.context_vars, **msg.context}
        self.coach = CoachEngine(self._emit_hint, language=msg.language,
                                 provider=self.provider, context_vars=self.context_vars)
        self.started = True
        if self.scenario.announcement:
            # Cold call : pas d'invitation, donc message d'information en début
            # d'appel. L'analyse émotionnelle attend sa confirmation.
            await self.coach.on_announcement(self.scenario.announcement, 0.0)
        else:
            self.emotions.start(0.0)
            await self.coach.on_step(self.tracker.step, 0.0)
        await self._send_state()

    @property
    def announcement_pending(self) -> bool:
        return bool(self.scenario.announcement) and self.announcement_at is None

    async def _on_announcement_done(self, ts: float) -> None:
        if not self.announcement_pending:
            return
        ts = max(ts, self.now)
        self.now = ts
        self.announcement_at = ts
        self.emotions.start(ts)
        self.tracker.step_started_at = ts
        await self.coach.on_step(self.tracker.step, ts)
        await self._send_state()

    async def _on_transcript(self, msg: TranscriptMsg) -> None:
        self.now = max(self.now, msg.end)
        if not msg.final:
            return  # les résultats partiels servent à l'affichage, pas aux règles
        self.metrics.add_segment(msg.speaker, msg.text, msg.start, msg.end)
        self.transcript.append(f"{msg.speaker.value}: {msg.text}")

        if self.announcement_pending:
            # Tant que le message d'information n'est pas confirmé, on ne fait
            # qu'écouter : pas d'avancée de scénario ni de conseil qui masquerait
            # le message (le « d'accord » du prospect y répond, il ne clôt pas l'accroche).
            await self._send_state()
            return

        rule_hint = None
        if msg.speaker is Speaker.PROSPECT:
            objection, advanced = self.tracker.on_prospect_text(msg.text, msg.end)
            if objection:
                rule_hint = await self.coach.on_objection(objection, msg.end)
            elif advanced:
                rule_hint = await self.coach.on_step(self.tracker.step, msg.end)
            # Full duplex : le LLM affine pendant que le vendeur lit déjà la règle.
            self.coach.request_llm(self._context(), msg.end, replaces=rule_hint)

        if self.tracker.step_overtime(self.now):
            await self.coach.on_overtime(self.tracker.step, self.now)
        await self.coach.on_metrics(self.metrics.snapshot(self.now), self.now)
        await self._send_state()

    async def _on_emotion_update(self, ts: float) -> None:
        self.now = max(self.now, ts)
        await self.coach.on_emotion(self.emotions.reading(Speaker.PROSPECT, self.now), self.now)
        await self._send_state()

    def _context(self) -> CoachContext:
        reading = self.emotions.reading(Speaker.PROSPECT, self.now)
        emotion = None
        if reading.state:
            emotion = f"{reading.state.value} ({reading.label}, {reading.confidence:.0%})"
        step = self.tracker.step
        return CoachContext(
            language=self.language,
            scenario_title=self.scenario.title,
            step_title=step.title,
            step_goal=step.goal,
            objection=self.tracker.active_objection.label if self.tracker.active_objection else None,
            prospect_emotion=emotion,
            metrics=self.metrics.snapshot(self.now).model_dump(),
            recent_transcript=list(self.transcript),
        )

    async def _send_state(self) -> None:
        speakers = [Speaker.PROSPECT] + ([Speaker.USER] if self.self_analysis else [])
        state = StateMsg(
            step=self.tracker.state(),
            metrics=self.metrics.snapshot(self.now),
            emotions=[self.emotions.reading(sp, self.now) for sp in speakers],
            analysis_paused=self.emotions.paused,
            announcement_pending=self.announcement_pending,
        )
        await self.send(state.model_dump(mode="json"))

    def summary(self) -> CallSummary:
        return CallSummary(
            duration=round(self.now, 1),
            steps_reached=list(self.tracker.steps_reached),
            objections=list(self.tracker.objections_seen),
            metrics=self.metrics.snapshot(self.now),
            hints_given=self.coach.hints_given,
            hints_useful=sum(self.feedback.values()),
            hints_not_useful=sum(not v for v in self.feedback.values()),
            announcement_at=self.announcement_at,
            emotion_analysis_used=self.emotions.samples_used > 0,
        )

    async def close(self) -> None:
        if self.started:
            await self.coach.close()
