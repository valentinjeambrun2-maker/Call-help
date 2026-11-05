"""Fournisseurs LLM pour les conseils fins. Remplaçables (Claude, open source...)."""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)

LIVE_MODEL = os.getenv("CALLHELP_LIVE_MODEL", "claude-haiku-5-5")
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


class LLMHint(BaseModel):
    kind: str
    text: str
    say: str | None
    confidence: float


class HintProvider(Protocol):
    async def suggest(self, ctx: CoachContext) -> LLMHint | None: ...


HINT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["objection", "step", "question", "emotion", "closing"]},
        "text": {"type": "string", "description": "Indication de 12 mots maximum"},
        "say": {"type": ["string", "null"], "description": "Phrase prête à dire, 25 mots maximum"},
        "confidence": {"type": "number"},
    },
    "required": ["kind", "text", "say", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """Tu es un coach commercial qui souffle des conseils à un vendeur pendant un appel en direct.
Le vendeur lit ton conseil en parlant : il doit pouvoir le lire en deux secondes.

Règles :
- `text` : une indication courte et actionnable (12 mots maximum).
- `say` : une phrase naturelle que le vendeur peut dire telle quelle, dans la langue de l'appel, ou null.
- Appuie-toi sur l'étape du scénario, l'objection en cours et l'état émotionnel probable du prospect.
- L'état émotionnel est une hypothèse : formule-la comme telle (« il semble hésiter »).
- N'invente aucun chiffre, client ou fait absent du contexte.
- `confidence` entre 0 et 1 : mets une valeur basse si le contexte ne justifie pas de conseil."""


class NullProvider:
    """Aucun LLM configuré : seuls les conseils par règles sont envoyés."""

    async def suggest(self, ctx: CoachContext) -> LLMHint | None:
        return None


class ClaudeProvider:
    def __init__(self, model: str = LIVE_MODEL) -> None:
        from anthropic import AsyncAnthropic  # dépendance optionnelle [llm]

        self.client = AsyncAnthropic()
        self.model = model

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
                    "format": {"type": "json_schema", "schema": HINT_SCHEMA},
                },
                messages=[{"role": "user", "content": ctx.model_dump_json(indent=1)}],
            )
        except anthropic.RateLimitError:
            log.warning("LLM rate limited, conseil ignoré")
            return None
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.warning("Erreur LLM, conseil ignoré : %s", exc)
            return None

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
