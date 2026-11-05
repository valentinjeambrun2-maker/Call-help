"""Moteur de conseils « full duplex » : règles instantanées + LLM en parallèle.

- Une règle répond tout de suite (< 100 ms) à un déclencheur.
- En parallèle, le LLM prépare un conseil plus fin qui remplace celui de la règle.
- Une nouvelle phrase du prospect annule la génération LLM devenue obsolète.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from ..models import EmotionReading, EmotionState, Hint, Metrics
from ..scenario import Objection, Step, fill
from .providers import CoachContext, HintProvider, LLMHint, NullProvider

log = logging.getLogger(__name__)

Emit = Callable[[Hint], Awaitable[None]]
# Appelé avec le classement du LLM (objection, étape) avant l'émission du conseil.
Classified = Callable[[LLMHint, float], Awaitable[None]]

EMOTION_HINTS = {
    EmotionState.HESITATION: ("Il semble hésiter : rassure et pose une question ouverte.",
                              "Qu'est-ce qui vous ferait dire oui, ou non, à ce stade ?"),
    EmotionState.ANNOYANCE: ("Il semble agacé : ralentis et reconnais son point.",
                             "Je comprends tout à fait, je vais aller à l'essentiel."),
    EmotionState.BOREDOM: ("Attention en baisse : change de rythme, implique-le.",
                           "Est-ce que je peux vous poser une question rapide ?"),
    EmotionState.ENTHUSIASM: ("Signal positif : c'est le moment de proposer le rendez-vous.", None),
    EmotionState.INTEREST: ("Il est attentif : approfondis ce point.", None),
}


class RateLimiter:
    """Au plus `max_hints` conseils par fenêtre glissante ; les urgents passent toujours."""

    def __init__(self, max_hints: int = 6, window: float = 20.0, cooldown: float = 15.0) -> None:
        self.max_hints = max_hints
        self.window = window
        self.cooldown = cooldown
        self.sent: deque[float] = deque()
        self.last_by_key: dict[str, float] = {}

    def allow(self, key: str, now: float, priority: int) -> bool:
        last = self.last_by_key.get(key)
        if last is not None and now - last < self.cooldown:
            return False
        while self.sent and now - self.sent[0] > self.window:
            self.sent.popleft()
        if priority < 3 and len(self.sent) >= self.max_hints:
            return False
        self.sent.append(now)
        self.last_by_key[key] = now
        return True


class CoachEngine:
    def __init__(
        self,
        emit: Emit,
        language: str = "fr",
        provider: HintProvider | None = None,
        context_vars: dict[str, str] | None = None,
        limiter: RateLimiter | None = None,
        llm_min_confidence: float = 0.6,
        on_classified: Classified | None = None,
        objection_ids: set[str] | None = None,
    ) -> None:
        self.emit = emit
        self.language = language
        self.provider = provider or NullProvider()
        self.context_vars = context_vars or {}
        self.limiter = limiter or RateLimiter()
        self.llm_min_confidence = llm_min_confidence
        self.on_classified = on_classified
        self.objection_ids = objection_ids or set()
        self._ids = itertools.count(1)
        self._llm_task: asyncio.Task | None = None
        self._last_emotion: EmotionState | None = None
        self._overtime_warned: set[str] = set()
        self.hints_given = 0

    # --- utilitaires ------------------------------------------------------

    def _say(self, says: dict[str, str]) -> str | None:
        return fill(says.get(self.language) or says.get("fr"), self.context_vars, self.language)

    async def _send(self, key: str, hint: Hint) -> Hint | None:
        if not self.limiter.allow(key, hint.ts, hint.priority):
            return None
        self.hints_given += 1
        await self.emit(hint)
        return hint

    def _new(self, **kw) -> Hint:
        return Hint(id=f"h{next(self._ids)}", **kw)

    # --- règles -----------------------------------------------------------

    async def on_objection(self, objection: Objection, ts: float) -> Hint | None:
        hint = self._new(kind="objection", text=objection.hint, say=self._say(objection.say),
                         priority=3, ts=ts, ref=objection.id)
        return await self._send(f"objection:{objection.id}", hint)

    async def on_announcement(self, says: dict[str, str], ts: float) -> Hint | None:
        hint = self._new(kind="announcement",
                         text="Avant tout : lis ce message et note la réponse du prospect.",
                         say=self._say(says), priority=3, ts=ts)
        return await self._send("announcement", hint)

    async def on_step(self, step: Step, ts: float) -> Hint | None:
        hint = self._new(kind="step", text=f"Étape « {step.title} » : {step.goal}",
                         say=self._say(step.say), priority=2, ts=ts, ref=step.id)
        return await self._send(f"step:{step.id}", hint)

    async def on_overtime(self, step: Step, ts: float) -> Hint | None:
        if step.id in self._overtime_warned:
            return None
        self._overtime_warned.add(step.id)
        hint = self._new(kind="step", text=f"« {step.title} » dure trop : passe à la suite.",
                         priority=2, ts=ts)
        return await self._send(f"overtime:{step.id}", hint)

    async def on_metrics(self, m: Metrics, ts: float) -> list[Hint]:
        sent = []
        if m.current_monologue >= 45:
            h = await self._send("pace:monologue", self._new(
                kind="pace", text=f"Tu parles depuis {int(m.current_monologue)} s : pose une question.",
                priority=2, ts=ts))
            sent.append(h)
        if m.user_wpm > 180 and m.elapsed > 20:
            h = await self._send("pace:fast", self._new(
                kind="pace", text=f"Débit élevé ({int(m.user_wpm)} mots/min) : ralentis.",
                priority=1, ts=ts))
            sent.append(h)
        if m.elapsed > 60 and m.user_talk_ratio > 0.7:
            h = await self._send("pace:ratio", self._new(
                kind="pace", text="Tu parles 70 % du temps : laisse-le s'exprimer.",
                priority=1, ts=ts))
            sent.append(h)
        return [h for h in sent if h]

    async def on_emotion(self, reading: EmotionReading, ts: float) -> Hint | None:
        if reading.state is None:
            self._last_emotion = None  # retour au neutre : un nouvel épisode sera signalé
            return None
        if reading.state == self._last_emotion:
            return None
        text, say = EMOTION_HINTS[reading.state]
        if reading.label == "probable":
            text = f"(probable) {text}"
        hint = self._new(kind="emotion", text=text, say=say, priority=1, ts=ts)
        sent = await self._send(f"emotion:{reading.state.value}", hint)
        if sent:  # refusé par la limite de fréquence : on réessaiera à la mesure suivante
            self._last_emotion = reading.state
        return sent

    # --- LLM en parallèle ---------------------------------------------------

    def request_llm(self, ctx: CoachContext, ts: float, replaces: Hint | None) -> None:
        """Lance un conseil LLM ; annule celui en cours, devenu obsolète."""
        if isinstance(self.provider, NullProvider):
            return
        if self._llm_task and not self._llm_task.done():
            self._llm_task.cancel()
        self._llm_task = asyncio.create_task(self._run_llm(ctx, ts, replaces))

    async def _run_llm(self, ctx: CoachContext, ts: float, replaces: Hint | None) -> None:
        try:
            result = await self.provider.suggest(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:  # un fournisseur défaillant ne doit jamais casser l'appel
            log.exception("Fournisseur LLM en erreur")
            return
        if result is None:
            return
        if result.objection not in self.objection_ids:
            result = result.model_copy(update={"objection": None})  # "other" ou inconnu : pas de fiche
        if self.on_classified:
            await self.on_classified(result, ts)
        if result.confidence < self.llm_min_confidence:
            return
        kind = result.kind if result.kind in ("objection", "step", "question", "emotion", "closing") else "question"
        ref = None
        priority = replaces.priority if replaces else 1
        if result.objection:
            kind, ref, priority = "objection", result.objection, 3
        hint = self._new(kind=kind, text=result.text, say=result.say, source="llm",
                         priority=priority, ref=ref or (replaces.ref if replaces else None),
                         replaces=replaces.id if replaces else None, ts=ts)
        if replaces and (not result.objection or replaces.ref == result.objection):
            # Affinage du conseil par règle : ne compte pas une seconde fois.
            await self.emit(hint)
        elif result.objection:
            # Objection que les mots-clés ont manquée (ou différente de la leur).
            hint = hint.model_copy(update={"replaces": None})
            await self._send(f"objection:{result.objection}", hint)
        else:
            await self._send(f"llm:{result.text}", hint)

    def cancel_llm(self) -> None:
        if self._llm_task and not self._llm_task.done():
            self._llm_task.cancel()

    async def close(self) -> None:
        if self._llm_task and not self._llm_task.done():
            self._llm_task.cancel()
            try:
                await self._llm_task
            except asyncio.CancelledError:
                pass
