"""Fournisseurs LLM pour les conseils fins. Remplaçables (Claude, open source...)."""

from __future__ import annotations

import json
import logging
import os
from typing import Literal, Protocol

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
    # "hint" : tour du prospect, conseil + classement. "step" : tour du vendeur, étape seule.
    task: Literal["hint", "step"] = "hint"


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


def step_schema(ctx: CoachContext) -> dict:
    """Tour du vendeur : l'étape seule. Sans conseil à écrire d'abord, l'étape décrit ce que le
    vendeur fait, et non ce que le conseil lui recommanderait (mesuré en démo : avec le conseil
    en tête, « Valeur » n'était presque jamais choisie, le conseil disant « creuse le besoin »)."""
    return {"type": "object", "properties": {
        "step": {"type": "string", "enum": [st["id"] for st in ctx.steps],
                 "description": "Étape d'après ce que le vendeur vient de faire"}},
        "required": ["step"], "additionalProperties": False}


def hint_schema(ctx: CoachContext) -> dict:
    """Schéma de sortie, avec les objections et étapes propres au scénario.

    Ordre voulu : l'étape (constat de ce que fait le vendeur), puis le conseil, puis le reste
    du classement. Les champs sont produits dans cet ordre, et le classement suit ce que le
    modèle vient d'écrire. Mesuré en démo : quand `objection` venait avant le conseil, celui-ci
    disait « objection budget » alors que le champ restait vide ; quand l'étape venait après,
    elle suivait le conseil (« creuse le besoin » -> qualification) et non l'appel."""
    objection_ids = [o["id"] for o in ctx.objections] + ["other"]
    step_ids = [st["id"] for st in ctx.steps]
    props: dict = {}
    if step_ids:
        props["step"] = {"type": "string", "enum": step_ids,
                         "description": "Étape d'après ce que fait le vendeur dans l'échange"}
    props |= {
        "text": {"type": "string", "description": "Indication de 12 mots maximum"},
        "say": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Phrase prête à dire, 25 mots maximum"},
        "kind": {"type": "string", "enum": ["objection", "step", "question", "emotion", "closing"]},
        "objection": {"anyOf": [{"type": "string", "enum": objection_ids}, {"type": "null"}],
                      "description": "Objection NOUVELLE exprimée dans le dernier tour du prospect, sinon null"},
    }
    props["prospect_state"] = {
        "anyOf": [{"type": "string", "enum": ["neutral", "interest", "hesitation", "annoyance", "boredom",
                                              "enthusiasm"]}, {"type": "null"}],
        "description": "État du prospect lu dans ses mots, null si emotion_allowed est faux"}
    props["state_confidence"] = {"type": "number"}
    props["confidence"] = {"type": "number"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SYSTEM_PROMPT = """Tu es un coach commercial qui souffle des conseils à un vendeur pendant un appel en direct.
Le vendeur lit ton conseil en parlant : il doit pouvoir le lire en deux secondes.

Règles :
- `text` : une indication courte et actionnable (12 mots maximum).
- `say` : une phrase naturelle que le vendeur peut dire telle quelle, dans la langue de l'appel, ou null.
- Appuie-toi sur l'étape du scénario, l'objection en cours et l'état émotionnel probable du prospect.
- L'état émotionnel est une hypothèse : formule-la comme telle (« il semble hésiter »).
- Ne déduis ni ne commente jamais l'état émotionnel ou le ton du vendeur (stress, nervosité,
  agacement…) : juge seulement ce qu'il dit et fait.
- Si `emotion_allowed` est faux, le prospect n'a pas accepté l'analyse : ne déduis, ne commente
  et n'utilise jamais son état émotionnel, ni à partir de sa voix ni à partir de ses mots.
  Appuie-toi uniquement sur le contenu de l'échange et sur le scénario.
- N'invente aucun chiffre, client ou fait absent du contexte.
- `confidence` entre 0 et 1 : mets une valeur basse si le contexte ne justifie pas de conseil.

Classement (en plus du conseil) :
- `objection` : une objection NOUVELLE que le prospect exprime dans son DERNIER tour, même
  indirectement (« je suis entre deux réunions » = manque de temps ; « ce n'est pas donné » =
  budget ; « on a déjà quelqu'un » = prestataire en place). Deux formes souvent manquées :
  - demander un mail ou une documentation au lieu de poursuivre ou de fixer un rendez-vous est
    une demande de mail, même dite poliment, en passant ou avec une autre demande ;
  - dire qu'il n'a pas le temps demandé (durée de l'appel ou du rendez-vous) sans proposer
    d'autre créneau est un manque de temps ; déplacer un créneau ne l'est pas.
  Choisis parmi `objections` du contexte, "other" pour une autre objection, null sinon.
  Mets null dans ces cas :
  - le prospect revient sur une objection déjà levée (`objections_raised`) ;
  - il accepte un rappel ou propose un créneau (« rappelez-moi mardi » quand on fixe un rendez-vous) ;
  - simple politesse, acquiescement ou fin d'appel (« d'accord », « au revoir », « mm-hmm »).
- `step` : l'étape où en est l'appel, parmi `steps` du contexte, d'après ce que le vendeur est en
  train de faire :
  - accroche : il se présente et demande un peu d'attention ;
  - qualification : il pose des questions sur la situation, les outils, les problèmes du prospect ;
  - valeur : il décrit ce qu'il propose, comment ça marche, ce que le prospect y gagnerait, un
    exemple ou un prix, y compris quand il répond à une objection. Un vendeur qui commence à
    expliquer son offre ou relie le problème entendu à sa solution est passé à la valeur, même
    s'il glisse encore une question ;
  - prise de rendez-vous : il propose explicitement un rendez-vous, un appel, une démo ou un
    créneau. Répondre à une objection n'est pas prendre rendez-vous.
  L'appel avance normalement dans cet ordre ; ne reviens pas en arrière.
- Si `task` vaut "step", le dernier tour est celui du vendeur : donne seulement `step`, d'après
  ce qu'il vient de dire.
- `prospect_state` : l'état du prospect tel qu'on le perçoit dans ses mots (dernier tour du prospect
  et contexte) : neutral, interest, hesitation, annoyance, boredom, enthusiasm ; `state_confidence`
  entre 0 et 1. C'est une hypothèse. Si `emotion_allowed` est faux : null et 0.
- Cohérence : si ton conseil répond à une objection (temps, budget, prestataire, demande de mail…),
  `kind` vaut "objection" ET `objection` contient son identifiant ; sinon `objection` vaut null.
  Le dernier tour du prospect peut être un fragment de sa phrase : juge-le avec ce qui précède."""


class NullProvider:
    """Aucun LLM configuré : seuls les conseils par règles sont envoyés."""

    async def suggest(self, ctx: CoachContext) -> LLMHint | None:
        return None


class ClaudeProvider:
    def __init__(self, model: str = LIVE_MODEL) -> None:
        from anthropic import AsyncAnthropic  # dépendance optionnelle [llm]

        # Délai court et une seule reprise : en direct, une réponse tardive ne sert plus.
        self.client = AsyncAnthropic(timeout=10.0, max_retries=1)
        self.model = model
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    async def suggest(self, ctx: CoachContext) -> LLMHint | None:
        import anthropic

        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=40 if ctx.task == "step" else 400,
                system=SYSTEM_PROMPT,
                # Latence d'abord : pas de réflexion pour un conseil de deux lignes.
                thinking={"type": "disabled"},
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema",
                               "schema": step_schema(ctx) if ctx.task == "step" else hint_schema(ctx)},
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
        data = json.loads(text)
        if ctx.task == "step":
            return LLMHint(kind="step", text="", say=None, confidence=0.0, step=data.get("step"))
        return LLMHint.model_validate(data)


def default_provider() -> HintProvider:
    if os.getenv("CALLHELP_LLM", "auto") == "off":
        return NullProvider()
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        try:
            return ClaudeProvider()
        except ImportError:
            log.warning("Paquet anthropic absent : pip install -e '.[llm]'")
    return NullProvider()
