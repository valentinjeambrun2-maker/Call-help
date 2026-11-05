"""Fournisseurs LLM pour les conseils fins. Remplaçables (Claude, open source...)."""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)

LIVE_MODEL = os.getenv("CALLHELP_LIVE_MODEL", "claude-haiku-5-5")
PRICES = {  # $ par million de jetons (entrée, sortie), tarif standard au 5 nov. 2026
    "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
}
SUMMARY_MODEL = os.getenv("CALLHELP_SUMMARY_MODEL", "claude-opus-5-5")


class CoachContext(BaseModel):
    language: str
    scenario_title: str
    step_title: str
    step_goal: str
    objection: str | None
    prospect_emotion: str | None
    metrics: dict
    recent_transcript: list[str]  # « user: ... » / « prospect: ... »
    knowledge: str = ""  # connaissance produit (base de connaissances, plus tard)
    # Faux si le prospect n'a pas donné son accord ou si l'analyse est coupée.
    emotion_allowed: bool = False
    # Pour classer le dernier tour du prospect : étapes et objections du scénario.
    steps: list[dict] = []  # [{"id", "title", "goal"}]
    objections: list[dict] = []  # [{"id", "label"}]
    current_step_id: str | None = None
    objections_raised: list[str] = []  # déjà levées dans cet appel : ne pas les reclasser


class LLMHint(BaseModel):
    kind: str
    text: str
    say: str | None
    confidence: float
    # Classement du dernier tour du prospect (complète les mots-clés).
    objection: str | None = None  # id d'objection, "other", ou None
    step: str | None = None  # étape où en est l'appel
    prospect_state: str | None = None  # état lu dans les mots du prospect (avec son accord seulement)
    state_confidence: float = 0.0


class HintProvider(Protocol):
    async def suggest(self, ctx: CoachContext) -> LLMHint | None: ...


def hint_schema(ctx: CoachContext) -> dict:
    """Schéma de sortie, avec les objections et étapes propres au scénario."""
    objection_ids = [o["id"] for o in ctx.objections] + ["other"]
    step_ids = [st["id"] for st in ctx.steps]
    props: dict = {
        "objection": {"anyOf": [{"type": "string", "enum": objection_ids}, {"type": "null"}],
                      "description": "Objection exprimée dans le DERNIER tour du prospect, sinon null"},
        "kind": {"type": "string", "enum": ["objection", "step", "question", "emotion", "closing"]},
        "text": {"type": "string", "description": "Indication de 12 mots maximum"},
        "say": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Phrase prête à dire, 25 mots maximum"},
        "confidence": {"type": "number"},
    }
    props["prospect_state"] = {
        "anyOf": [{"type": "string", "enum": ["neutral", "interest", "hesitation", "annoyance", "boredom",
                                              "enthusiasm"]}, {"type": "null"}],
        "description": "État du prospect lu dans ses mots, null si emotion_allowed est faux"}
    props["state_confidence"] = {"type": "number"}
    required = ["objection", "kind", "text", "say", "confidence", "prospect_state", "state_confidence"]
    if step_ids:
        props["step"] = {"type": "string", "enum": step_ids, "description": "Étape où en est l'appel"}
        required.insert(1, "step")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}

SYSTEM_PROMPT = """Tu es un coach commercial qui souffle des conseils à un vendeur pendant un appel en direct.
Le vendeur lit ton conseil en parlant : il doit pouvoir le lire en deux secondes.

Règles :
- `text` : une indication courte et actionnable (12 mots maximum).
- `say` : une phrase naturelle que le vendeur peut dire telle quelle, dans la langue de l'appel, ou null.
- Appuie-toi sur l'étape du scénario, l'objection en cours et l'état émotionnel probable du prospect.
- L'état émotionnel est une hypothèse : formule-la comme telle (« il semble hésiter »).
- Si `emotion_allowed` est faux, le prospect n'a pas accepté l'analyse : ne déduis, ne commente
  et n'utilise jamais son état émotionnel, ni à partir de sa voix ni à partir de ses mots.
  Appuie-toi uniquement sur le contenu de l'échange et sur le scénario.
- N'invente aucun chiffre, client ou fait absent du contexte.
- `confidence` entre 0 et 1 : mets une valeur basse si le contexte ne justifie pas de conseil.

Classement (en plus du conseil) :
- `objection` : une objection NOUVELLE que le prospect exprime dans son DERNIER tour, même
  indirectement (« je suis entre deux réunions » = manque de temps ; « ce n'est pas donné » =
  budget ; « on a déjà quelqu'un » = prestataire en place). Choisis parmi `objections` du
  contexte, "other" pour une autre objection, null sinon. Mets null dans ces cas :
  - le prospect revient sur une objection déjà levée (`objections_raised`) ;
  - il accepte un rappel ou propose un créneau (« rappelez-moi mardi » quand on fixe un rendez-vous) ;
  - simple politesse, acquiescement ou fin d'appel (« d'accord », « au revoir », « mm-hmm »).
- `step` : l'étape où en est l'appel, parmi `steps` du contexte, d'après ce que le vendeur est en
  train de faire : se présenter et capter l'attention (accroche), questionner sur la situation
  (qualification), présenter son offre ou un bénéfice (valeur), proposer un rendez-vous ou un
  créneau (prise de rendez-vous). L'appel avance normalement dans cet ordre ; ne reviens pas en arrière.
- Si le DERNIER tour est celui du vendeur, `objection` vaut null (seul `step` compte).
- `prospect_state` : l'état du prospect tel qu'on le perçoit dans ses mots (dernier tour du prospect
  et contexte) : neutral, interest, hesitation, annoyance, boredom, enthusiasm ; `state_confidence`
  entre 0 et 1. C'est une hypothèse. Si `emotion_allowed` est faux : null et 0.
- Si `objection` n'est pas null, `kind` vaut "objection" et le conseil y répond."""


class NullProvider:
    """Aucun LLM configuré : seuls les conseils par règles sont envoyés."""

    async def suggest(self, ctx: CoachContext) -> LLMHint | None:
        return None


class ClaudeProvider:
    def __init__(self, model: str = LIVE_MODEL) -> None:
        from anthropic import AsyncAnthropic  # dépendance optionnelle [llm]

        self.client = AsyncAnthropic()
        self.model = model
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    async def suggest(self, ctx: CoachContext) -> LLMHint | None:
        import anthropic

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=400,
                system=SYSTEM_PROMPT,
                # Latence d'abord : pas de réflexion pour un conseil de deux lignes.
                thinking={"type": "disabled"},
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": hint_schema(ctx)},
                },
                messages=[{"role": "user", "content": ctx.model_dump_json(indent=1)}],
            )
        except anthropic.RateLimitError:
            log.warning("LLM rate limited, conseil ignoré")
            return None
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.warning("Erreur LLM, conseil ignoré : %s", exc)
            return None

        self.usage["requests"] += 1
        self.usage["input_tokens"] += response.usage.input_tokens or 0
        self.usage["output_tokens"] += response.usage.output_tokens or 0
        if response.stop_reason in ("refusal", "max_tokens"):
            log.info("Conseil LLM non exploitable (stop_reason=%s)", response.stop_reason)
            return None
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return None
        return LLMHint.model_validate(json.loads(text))


def default_provider() -> HintProvider:
    if os.getenv("CALLHELP_LLM", "auto") == "off":
        return NullProvider()
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        try:
            return ClaudeProvider()
        except ImportError:
            log.warning("Paquet anthropic absent : pip install -e '.[llm]'")
    return NullProvider()
