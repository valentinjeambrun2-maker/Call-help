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
# Appelé avec le classement du LLM (objection, étape) avant l'émission du conseil :
# (résultat, fin du tour, tour du prospect ?). `on_unclassified(ts)` si le LLM n'a pas répondu.
Classified = Callable[[LLMHint, float, bool], Awaitable[None]]

# Indication (toujours en français : c'est l'interface du vendeur) et phrase à dire
# (dans la langue de l'appel).
EMOTION_HINTS = {
    EmotionState.HESITATION: ("Il semble hésiter : rassure et pose une question ouverte.",
                              {"fr": "Qu'est-ce qui vous ferait dire oui, ou non, à ce stade ?",
                               "en": "What would make this a yes, or a no, for you at this point?"}),
    EmotionState.ANNOYANCE: ("Il semble agacé : ralentis et reconnais son point.",
                             {"fr": "Je comprends tout à fait, je vais aller à l'essentiel.",
                              "en": "I completely understand, I'll get straight to the point."}),
    EmotionState.BOREDOM: ("Attention en baisse : change de rythme, implique-le.",
                           {"fr": "Est-ce que je peux vous poser une question rapide ?",
                            "en": "Can I ask you one quick question?"}),
    EmotionState.ENTHUSIASM: ("Signal positif : c'est le moment de proposer le rendez-vous.", None),
    EmotionState.INTEREST: ("Il est attentif : approfondis ce point.", None),
}


class RateLimiter:
    """Au plus `max_hints` conseils par fenêtre glissante ; les urgents passent toujours.
    Une même objection n'est pas re-signalée avant `objection_cooldown` secondes : le
    vendeur a déjà la réponse, et le prospect y revient souvent (62 % des fausses alertes
    mesurées sur le pilote de 100 appels)."""

    def __init__(self, max_hints: int = 6, window: float = 20.0, cooldown: float = 15.0,
                 objection_cooldown: float = 90.0) -> None:
        self.max_hints = max_hints
        self.window = window
        self.cooldown = cooldown
        self.objection_cooldown = objection_cooldown
        self.sent: deque[float] = deque()
        self.last_by_key: dict[str, float] = {}

    def allow(self, key: str, now: float, priority: int) -> bool:
        last = self.last_by_key.get(key)
        cooldown = self.objection_cooldown if key.startswith("objection:") else self.cooldown
        if last is not None and now - last < cooldown:
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
        on_classified=None,
        objection_ids: set[str] | None = None,
        on_unclassified=None,
    ) -> None:
        self.emit = emit
        self.language = language
        self.provider = provider or NullProvider()
        self.context_vars = context_vars or {}
        self.limiter = limiter or RateLimiter()
        self.llm_min_confidence = llm_min_confidence
        self.on_classified = on_classified
        self.on_unclassified = on_unclassified
        self.objection_ids = objection_ids or set()
        self._ids = itertools.count(1)
        self._llm_task: asyncio.Task | None = None  # dernière analyse lancée
        self._llm_tasks: set[asyncio.Task] = set()  # analyses en cours (en parallèle)
        self._classify_tasks: set[asyncio.Task] = set()  # dont classements d'un tour du vendeur
        self.emotion_suppressed = False  # « Couper l'analyse » : plus de conseil fondé sur le ressenti
        self._llm_generation = 0
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
        text, says = EMOTION_HINTS[reading.state]
        say = (says.get(self.language) or says["fr"]) if says else None
        if reading.label == "probable":
            text = f"(probable) {text}"
        hint = self._new(kind="emotion", text=text, say=say, priority=1, ts=ts)
        sent = await self._send(f"emotion:{reading.state.value}", hint)
        if sent:  # refusé par la limite de fréquence : on réessaiera à la mesure suivante
            self._last_emotion = reading.state
        return sent

    # --- LLM en parallèle ---------------------------------------------------

    MAX_LLM_INFLIGHT = 6

    @property
    def has_llm(self) -> bool:
        return not isinstance(self.provider, NullProvider)

    def retract_objection(self, objection_id: str) -> None:
        """Objection des mots-clés infirmée par le LLM : elle ne bloque plus la vraie."""
        self.limiter.last_by_key.pop(f"objection:{objection_id}", None)

    LLM_TIMEOUT = 8.0  # s : au-delà, le classement arrive trop tard pour être utile en direct

    def request_llm(self, ctx: CoachContext, ts: float, replaces: Hint | None, classify_only: bool = False) -> None:
        """Lance l'analyse du dernier tour par le LLM, en parallèle des précédentes.

        On n'annule plus l'analyse en cours quand le prospect reparle : en direct, il
        reparle souvent avant le retour du LLM (~2-4 s), et l'annulation faisait perdre
        le classement du tour qui contenait l'objection (0 objection repérée en démo).
        Les tours du prospect sont prioritaires sur ceux du vendeur (classement d'étape seul)."""
        if isinstance(self.provider, NullProvider):
            return
        if classify_only and any(t in self._classify_tasks for t in self._llm_tasks):
            return  # un classement du vendeur est déjà en cours : inutile d'en empiler
        if len(self._llm_tasks) >= self.MAX_LLM_INFLIGHT:
            seller = [t for t in self._llm_tasks if t in self._classify_tasks]
            if classify_only or not seller:
                log.warning("Trop d'analyses LLM en cours : tour ignoré")
                if not classify_only and self.on_unclassified:
                    asyncio.get_running_loop().create_task(self.on_unclassified(ts))
                return
            seller[0].cancel()  # on sacrifie un classement d'étape, pas un tour du prospect
        if not classify_only:
            self._llm_generation += 1
        task = asyncio.create_task(self._run_llm(ctx, ts, replaces, self._llm_generation, classify_only))
        self._llm_tasks.add(task)
        if classify_only:
            self._classify_tasks.add(task)
            task.add_done_callback(self._classify_tasks.discard)
        task.add_done_callback(self._llm_tasks.discard)
        self._llm_task = task

    async def drain_llm(self, timeout: float) -> None:
        """Laisse les analyses en cours se terminer (fin d'appel), puis annule les retardataires."""
        tasks = list(self._llm_tasks)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def wait_llm(self) -> None:
        """Attend toutes les analyses en cours (évaluation, tests)."""
        while self._llm_tasks:
            await asyncio.gather(*list(self._llm_tasks), return_exceptions=True)

    async def _run_llm(self, ctx: CoachContext, ts: float, replaces: Hint | None, generation: int = 0,
                       classify_only: bool = False) -> None:
        try:
            result = await asyncio.wait_for(self.provider.suggest(ctx), self.LLM_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.warning("LLM trop lent (> %.0f s) : tour non classé", self.LLM_TIMEOUT)
            result = None
        except Exception:  # un fournisseur défaillant ne doit jamais casser l'appel
            log.exception("Fournisseur LLM en erreur")
            result = None
        if result is None:
            # Panne, quota, délai : la session retombe sur les mots-clés pour ce tour.
            if not classify_only and self.on_unclassified:
                await self.on_unclassified(ts)
            return
        if classify_only or result.objection not in self.objection_ids:
            result = result.model_copy(update={"objection": None})  # "other", inconnu ou tour du vendeur
        if self.on_classified:
            await self.on_classified(result, ts, not classify_only)  # peut confirmer l'objection des mots-clés
        if classify_only:
            return
        if self.emotion_suppressed and result.kind == "emotion":
            return  # analyse coupée : pas de conseil fondé sur le ressenti
        keyword_card = replaces is not None and replaces.kind == "objection"
        # Le LLM infirme la fiche d'objection des mots-clés : on la remplace toujours à l'écran,
        # même avec une faible confiance, pour ne pas laisser une fausse alerte affichée.
        overrides = keyword_card and result.objection is None
        if keyword_card and result.objection and result.objection != replaces.ref:
            # Le LLM nomme une AUTRE objection (réponse fusionnée qui en contient deux, « trop cher…
            # et on a déjà une agence ») : la session garde les deux, la fiche des mots-clés reste
            # affichée et la sienne passe comme une nouvelle fiche (filtrée si déjà montrée).
            replaces = None
        if result.confidence < self.llm_min_confidence and not overrides:
            return
        # Un tour plus récent a déjà été envoyé au LLM : le classement ci-dessus reste
        # utile, mais un conseil générique sur un tour dépassé n'est plus affiché
        # (sauf objection, ou affinage d'un conseil par règle).
        stale = generation < self._llm_generation
        if stale and not result.objection and not replaces:
            return
        kind = result.kind if result.kind in ("objection", "step", "question", "emotion", "closing") else "question"
        ref = None
        priority = replaces.priority if replaces else 1
        if result.objection:
            kind, ref, priority = "objection", result.objection, 3
        hint = self._new(kind=kind, text=result.text, say=result.say, source="llm",
                         priority=priority, ref=ref, replaces=replaces.id if replaces else None, ts=ts)
        if replaces:
            # Affinage ou correction du conseil par règle de ce tour : il le remplace à l'écran
            # (une fausse alerte des mots-clés disparaît) et ne compte pas une seconde fois.
            await self.emit(hint)
        elif result.objection:
            # Objection que les mots-clés ont manquée.
            await self._send(f"objection:{result.objection}", hint)
        else:
            await self._send(f"llm:{result.text}", hint)

    def cancel_llm(self) -> None:
        for task in list(self._llm_tasks):
            task.cancel()

    async def close(self) -> None:
        tasks = list(self._llm_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
