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
    PauseAnalysisMsg, Speaker, StartMsg, StateMsg, StopMsg, SummaryMsg, TranscriptEcho, TranscriptMsg, VoiceMsg,
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
        self.prospect_consent: bool | None = None
        self.replaced: dict[str, str] = {}  # conseil IA -> conseil par règle qu'il remplace
        self.provisional: dict[float, str] = {}  # fin de tour -> objection des mots-clés à confirmer
        self.stt = None  # SttHub quand le backend transcrit lui-même

    async def _emit_hint(self, hint: Hint) -> None:
        if hint.replaces:
            self.replaced[hint.id] = self.replaced.get(hint.replaces, hint.replaces)
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
            # Un conseil affiné par l'IA et celui qu'il remplace ne comptent qu'une fois.
            self.feedback[self.replaced.get(msg.hint_id, msg.hint_id)] = msg.useful
        elif isinstance(msg, PauseAnalysisMsg):
            self.emotions.paused = True
            # Un conseil IA en cours a pu être construit avec l'émotion du prospect.
            self.coach.cancel_llm()
            await self._send_state()
        elif isinstance(msg, AnnouncementDoneMsg):
            await self._on_announcement_done(msg.ts, msg.consent)
        elif isinstance(msg, StopMsg):
            if self.stt is not None:
                await self.stt.close()  # derniers énoncés transcrits avant le bilan
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
                                 provider=self.provider, context_vars=self.context_vars,
                                 on_classified=self._on_classified,
                                 objection_ids={o.id for o in self.scenario.objections})
        self.started = True
        if msg.transcription == "server":
            await self._start_stt(msg)
        if self.scenario.announcement:
            # Cold call : pas d'invitation, donc message d'information en début
            # d'appel. L'analyse émotionnelle attend sa confirmation.
            await self.coach.on_announcement(self.scenario.announcement, 0.0)
        else:
            # Scénario sans message (meeting) : l'accord a été recueilli à l'invitation.
            self.prospect_consent = True
            self.emotions.start(0.0)
            await self.coach.on_step(self.tracker.step, 0.0)
        await self._send_state()

    async def _start_stt(self, msg: StartMsg) -> None:
        import os

        from .stt import SttHub

        key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not key:
            await self.send({"type": "error", "message": "Transcription en direct indisponible : "
                                                        "ELEVENLABS_API_KEY absente du backend."})
            return
        names = msg.keyterms + [v for k, v in self.context_vars.items() if k in ("company", "user_name")]
        self.stt = SttHub(key, msg.language, self._on_stt_transcript, self._on_stt_status, keyterms=names)
        self.stt.start()

    def push_audio(self, data: bytes) -> bool:
        """Trame audio binaire du client (1 octet de canal + PCM 16 kHz).
        Retourne False si la transcription en direct n'est pas active pour cet appel."""
        if self.stt is None:
            return False
        self.stt.push(data)
        return True

    async def _on_stt_transcript(self, msg: TranscriptMsg) -> None:
        await self.send(TranscriptEcho(**msg.model_dump(exclude={"type"})).model_dump(mode="json"))
        if msg.final:
            await self._on_transcript(msg)

    async def _on_stt_status(self, level: str, message: str) -> None:
        if level == "error":
            await self.send({"type": "error", "message": message})

    @property
    def announcement_pending(self) -> bool:
        return bool(self.scenario.announcement) and self.announcement_at is None

    async def _on_announcement_done(self, ts: float, consent: bool) -> None:
        if not self.announcement_pending:
            return
        ts = max(ts, self.now)
        self.now = ts
        self.announcement_at = ts
        self.prospect_consent = consent
        # Le refus du prospect porte sur lui seul : l'analyse du ton de l'utilisateur,
        # qu'il a lui-même choisie, continue.
        if not consent:
            self.emotions.blocked.add(Speaker.PROSPECT)
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
            if self.coach.has_llm:
                # Le LLM fait autorité : les mots-clés donnent un conseil instantané mais
                # provisoire, que le classement du LLM (~2-3 s) confirme ou retire. Les
                # étapes n'avancent plus sur un simple « oui » (mesuré en démo).
                objection = self.tracker.detect_objection(msg.text)
                if objection:
                    rule_hint = await self.coach.on_objection(objection, msg.end)
                    self.provisional[msg.end] = objection.id
            else:
                objection, advanced = self.tracker.on_prospect_text(msg.text, msg.end)
                if objection:
                    rule_hint = await self.coach.on_objection(objection, msg.end)
                elif advanced:
                    rule_hint = await self.coach.on_step(self.tracker.step, msg.end)
            # Full duplex : le LLM affine pendant que le vendeur lit déjà la règle.
            self.coach.request_llm(self._context(), msg.end, replaces=rule_hint)
        elif self.coach.has_llm:
            # Tour du vendeur : classement seul (l'étape suit ce qu'il est en train de faire).
            self.coach.request_llm(self._context(), msg.end, replaces=None, classify_only=True)

        if self.tracker.step_overtime(self.now):
            await self.coach.on_overtime(self.tracker.step, self.now)
        await self.coach.on_metrics(self.metrics.snapshot(self.now), self.now)
        await self._send_state()

    async def _on_emotion_update(self, ts: float) -> None:
        self.now = max(self.now, ts)
        await self.coach.on_emotion(self.emotions.reading(Speaker.PROSPECT, self.now), self.now)
        await self._send_state()

    async def _on_classified(self, result, ts: float) -> None:
        """Classement du LLM : confirme ou corrige les mots-clés (objection), fait avancer l'étape."""
        changed = False
        provisional = self.provisional.pop(ts, None)
        if provisional and provisional != result.objection:
            # Fausse alerte des mots-clés : elle ne compte pas comme objection levée et ne
            # bloque pas la vraie objection de ce type plus tard.
            self.coach.retract_objection(provisional)
        if result.objection:
            changed |= self.tracker.mark_objection(result.objection)
        if result.step and self.tracker.advance_to(result.step, ts):
            changed = True
            await self.coach.on_step(self.tracker.step, ts)
        if changed:
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
            emotion_allowed=bool(self.prospect_consent) and self.emotions.running,
            steps=[{"id": st.id, "title": st.title, "goal": st.goal} for st in self.scenario.steps],
            objections=[{"id": o.id, "label": o.label} for o in self.scenario.objections],
            current_step_id=step.id,
            objections_raised=list(self.tracker.objections_seen),
        )

    async def _send_state(self) -> None:
        speakers = [Speaker.PROSPECT] + ([Speaker.USER] if self.self_analysis else [])
        state = StateMsg(
            step=self.tracker.state(),
            metrics=self.metrics.snapshot(self.now),
            emotions=[self.emotions.reading(sp, self.now) for sp in speakers],
            analysis_paused=self.emotions.paused,
            announcement_pending=self.announcement_pending,
            prospect_consent=self.prospect_consent,
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
            prospect_consent=self.prospect_consent,
            transcription_cost_usd=round(self.stt.cost_usd, 4) if self.stt is not None else None,
            emotion_analysis_used=self.emotions.samples_used > 0,
        )

    async def close(self) -> None:
        if self.stt is not None:
            await self.stt.close()
        if self.started:
            await self.coach.close()
