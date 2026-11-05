"""Bilan de fin d'appel rédigé par un modèle plus fort que celui des conseils en direct.

Contenu : résumé, objections et façon dont elles ont été traitées, prochaine étape, email
de suivi prêt à relire (dans la langue de l'appel), points forts et axes de progrès.
Aucun état émotionnel n'y figure : le bilan peut être partagé, et la lecture du ton reste
une aide en direct (docs/decisions.md, Q38).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from pydantic import BaseModel

from .providers import PRICES, SUMMARY_MODEL

log = logging.getLogger(__name__)


class ObjectionReview(BaseModel):
    objection: str
    handling: str  # « bien traitée », « partiellement », « à retravailler »
    comment: str


class FollowUpEmail(BaseModel):
    subject: str
    body: str


class AiSummary(BaseModel):
    summary: str
    outcome: str
    next_step: str
    objections: list[ObjectionReview]
    strengths: list[str]
    improvements: list[str]
    follow_up_email: FollowUpEmail


class SummaryInput(BaseModel):
    language: str
    scenario_title: str
    seller_company: str | None
    steps: list[str]
    steps_reached: list[str]
    objections_raised: list[str]
    duration_seconds: float
    transcript: list[str]  # « [mm:ss] vendeur : ... »


class SummaryProvider(Protocol):
    async def summarize(self, data: SummaryInput) -> AiSummary | None: ...


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "3 à 5 phrases"},
        "outcome": {"type": "string", "description": "issue de l'appel en quelques mots"},
        "next_step": {"type": "string"},
        "objections": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "objection": {"type": "string"},
                "handling": {"type": "string", "enum": ["bien traitée", "partiellement", "à retravailler"]},
                "comment": {"type": "string"},
            },
            "required": ["objection", "handling", "comment"], "additionalProperties": False}},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
        "follow_up_email": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
                            "required": ["subject", "body"], "additionalProperties": False},
    },
    "required": ["summary", "outcome", "next_step", "objections", "strengths", "improvements", "follow_up_email"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu es un coach commercial expérimenté. À partir de la transcription d'un appel de prospection, tu rédiges le bilan que le vendeur lira juste après avoir raccroché.

Règles :
- Analyse en français, concrète et brève. Appuie chaque remarque sur ce qui a été dit (cite une courte phrase si utile).
- `objections` : chaque objection réellement exprimée par le prospect, et la façon dont le vendeur l'a traitée.
- `strengths` et `improvements` : 2 ou 3 éléments chacun, formulés comme des actions (« Reformule le besoin avant de pitcher »).
- `follow_up_email` : un email de suivi prêt à relire, dans la LANGUE DE L'APPEL, cohérent avec l'issue (confirmation de rendez-vous, envoi d'informations demandé, rappel convenu, ou remerciement bref si refus). N'invente ni chiffre, ni référence client, ni engagement qui n'a pas été pris pendant l'appel.
- Ne commente jamais l'état émotionnel ni le ton de quiconque, prospect ou vendeur (ni stress, ni nervosité, ni agacement) : ce bilan peut être partagé. Juge seulement ce qui a été dit et fait.
- La transcription peut contenir des erreurs de reconnaissance vocale : interprète avec bon sens."""


class NullSummary:
    async def summarize(self, data: SummaryInput) -> AiSummary | None:
        return None


class ClaudeSummary:
    def __init__(self, model: str = SUMMARY_MODEL) -> None:
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic()
        self.model = model
        self.cost_usd = 0.0
        self.last_error: str | None = None  # raison lisible d'un échec, pour le vendeur

    async def summarize(self, data: SummaryInput) -> AiSummary | None:
        import anthropic

        try:
            msg = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
                # Repli serveur en cas de refus (modèle Opus) : l'API relance sur le modèle adapté.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": data.model_dump_json(indent=1)}],
            )
        except anthropic.AuthenticationError as exc:
            self.last_error = "clé Anthropic refusée"
            log.error("Bilan IA indisponible : %s", exc)
            return None
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.warning("Bilan IA indisponible : %s", exc)
            return None
        pin, pout = PRICES.get(self.model, (0, 0))
        self.cost_usd += ((msg.usage.input_tokens or 0) * pin + (msg.usage.output_tokens or 0) * pout) / 1e6
        if msg.stop_reason != "end_turn":
            log.info("Bilan IA non exploitable (stop_reason=%s)", msg.stop_reason)
            return None
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            return AiSummary.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            log.warning("Bilan IA invalide")
            return None


def default_summary_provider() -> SummaryProvider:
    if os.getenv("CALLHELP_LLM", "auto") == "off":
        return NullSummary()
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        try:
            return ClaudeSummary()
        except ImportError:
            pass
    return NullSummary()
