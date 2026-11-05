"""Écriture des dialogues par Claude, avec étiquettes par tour.

Deux modes :
- `batch` (1 000 appels) : Message Batches API, moitié prix, résultats en < 24 h ;
- `sync` (pilote) : appels directs en parallèle, avec repli serveur en cas de refus.

Chaque tour porte : locuteur, texte prononcé, émotion + intensité, indications
de jeu (traduites en balises ElevenLabs à l'étape voix), étape du scénario,
objection éventuelle, pause et chevauchement avec le tour précédent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .plan import OBJECTIONS, STATES, CallSpec

log = logging.getLogger(__name__)

DIALOGUE_MODEL = os.getenv("CALLHELP_DIALOGUE_MODEL", "claude-opus-5-5")
WORDS_PER_MINUTE = 150

STEPS = ("announcement", "hook", "qualify", "value", "close", "wrap_up")

# Indications de jeu neutres vis-à-vis du fournisseur de voix ; datagen/voices.py
# les traduit en balises (ex. [hesitant]) ou en réglages selon le modèle.
DELIVERY = (
    "hesitant", "annoyed", "excited", "bored", "curious", "warm", "rushed", "calm",
    "thoughtful", "skeptical", "sighs", "laughs", "clears_throat", "interrupting",
)

OFFERS = {
    "fr": ["refonte de site web", "référencement SEO et visibilité locale", "logiciel métier sur mesure",
           "automatisation par IA des tâches administratives", "identité de marque"],
    "en": ["website redesign", "SEO and local visibility", "custom business software",
           "AI automation of admin tasks", "brand identity"],
}


class Turn(BaseModel):
    speaker: Literal["seller", "prospect"]
    text: str = Field(min_length=1)
    emotion: Literal[STATES]  # type: ignore[valid-type]
    intensity: float = Field(ge=0, le=1)
    delivery: list[Literal[DELIVERY]] = []  # type: ignore[valid-type]
    step: Literal[STEPS]  # type: ignore[valid-type]
    objection: Literal[tuple(OBJECTIONS) + ("other",)] | None = None  # type: ignore[valid-type]
    pause_ms: int = Field(ge=0, le=4000)
    overlap_ms: int = Field(ge=0, le=1500)


class Dialogue(BaseModel):
    call_id: str
    seller_name: str
    prospect_name: str
    prospect_company: str
    offer: str
    turns: list[Turn] = Field(min_length=6)
    outcome: Literal["meeting_booked", "callback", "send_info", "refused", "hung_up"]


# Schéma JSON pour les sorties structurées (sans call_id, ajouté ensuite).
DIALOGUE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "seller_name": {"type": "string"},
        "prospect_name": {"type": "string"},
        "prospect_company": {"type": "string"},
        "offer": {"type": "string"},
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["seller", "prospect"]},
                    "text": {"type": "string"},
                    "emotion": {"type": "string", "enum": list(STATES)},
                    "intensity": {"type": "number"},
                    "delivery": {"type": "array", "items": {"type": "string", "enum": list(DELIVERY)}},
                    "step": {"type": "string", "enum": list(STEPS)},
                    "objection": {"anyOf": [{"type": "string", "enum": list(OBJECTIONS) + ["other"]},
                                            {"type": "null"}]},
                    "pause_ms": {"type": "integer"},
                    "overlap_ms": {"type": "integer"},
                },
                "required": ["speaker", "text", "emotion", "intensity", "delivery", "step",
                             "objection", "pause_ms", "overlap_ms"],
                "additionalProperties": False,
            },
        },
        "outcome": {"type": "string",
                    "enum": ["meeting_booked", "callback", "send_info", "refused", "hung_up"]},
    },
    "required": ["seller_name", "prospect_name", "prospect_company", "offer", "turns", "outcome"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You write realistic, fully fictional B2B cold-call transcripts used to test and train a real-time sales-call assistant. The assistant listens to both sides, tracks the call steps, detects objections and reads each speaker's emotional state from their voice. Your transcripts are later voiced by a text-to-speech engine, so every turn must sound like natural spoken language in the requested language, not written prose.

Write the call so it is useful as labelled data:
- Spoken style: short sentences, false starts and self-repairs, fillers ("euh", "bon", "ben", "hum" in French; "uh", "well", "um" in English), a few interruptions. Aim for rates measured in real speech: in French about 3 filled pauses ("euh") and 3 repetitions per 100 words; in English fewer filled pauses. Make 5 to 10 % of turns short backchannels on their own ("mm-hmm", "d'accord", "oui oui", "ouais", "right", "okay"). Junior sellers talk too fast, pitch too early and sometimes deliver long monologues; senior sellers ask open questions and listen.
- Objections: phrase every objection in your own words, the way that persona would say it. Vary the wording a lot; never reuse stock sentences. An objection may be indirect ("on verra plus tard", "c'est pas vraiment le moment").
- Emotions: the label of a turn is the emotion of the speaker of that turn, as a listener would perceive it from tone and words. Express it through word choice, rhythm and the delivery hints; do not have speakers name their own emotions. Follow the prospect's emotional arc given in the brief across the call (start, middle, end), with natural fluctuations. Use "neutral" when nothing stands out. Intensity is 0.2 (subtle) to 1.0 (very strong).
- Steps: label each turn with the call step it belongs to: announcement (the seller's short notice that an assistant analyses the call, said right after the greeting; the prospect may react), hook, qualify, value, close, wrap_up. Steps move forward; an objection can happen at any step and keeps that step's label.
- objection: the objection id on the prospect turn that raises it (no_time, has_provider, no_budget, send_email, or "other" for any other objection), otherwise null.
- pause_ms: silence before the turn (100-600 typical, up to 2500 when someone hesitates or thinks). overlap_ms: how early the turn starts before the previous one ends (0 unless interrupting; 150-900 for interruptions).
- delivery: zero to three hints per turn from the allowed list that a voice actor would need.
- Everything is fictional: invent person and company names; never use real people, real companies (except the seller's company given in the brief), real phone numbers or emails.
- The call must end with the requested outcome and last roughly the requested number of words."""


def build_brief(spec: CallSpec, seller_company: str = "The Herakles") -> str:
    import random

    rng = random.Random(spec.seed)
    offer = rng.choice(OFFERS[spec.language])
    words = int(spec.target_minutes * WORDS_PER_MINUTE)
    language = "French (France)" if spec.language == "fr" else "English"
    objections = ", ".join(spec.objections) if spec.objections else "none (the prospect raises no objection)"
    return f"""Write one cold call.

Language: {language}
Seller: {spec.seller_level} salesperson ({spec.seller_gender}) at {seller_company}, a digital studio. Offer: {offer}.
Prospect: {spec.prospect_role.replace('_', ' ')} ({spec.prospect_gender}); business: {spec.sector}; personality: {spec.prospect_personality}.
Objections the prospect raises, in this order: {objections}.
Prospect emotional arc (start -> middle -> end): {' -> '.join(spec.arc)}.
Outcome: {spec.outcome}.
Length: about {words} spoken words in total ({spec.target_minutes} minutes).
Return the offer you used in "offer"."""


def validate_dialogue(spec: CallSpec, d: Dialogue) -> list[str]:
    """Contrôles de cohérence. Une liste vide = dialogue accepté."""
    issues = []
    words = sum(len(t.text.split()) for t in d.turns)
    target = spec.target_minutes * WORDS_PER_MINUTE
    if not 0.6 * target <= words <= 1.5 * target:
        issues.append(f"longueur {words} mots pour {int(target)} visés")
    # Le message d'information suit la salutation : il est hors de l'ordre des étapes.
    order = [STEPS.index(t.step) for t in d.turns if t.step != "announcement"]
    if any(b < a for a, b in zip(order, order[1:])):
        issues.append("les étapes reculent")
    raised = [t.objection for t in d.turns if t.objection and t.speaker == "prospect"]
    missing = [o for o in spec.objections if o not in raised]
    if missing:
        issues.append(f"objections absentes : {missing}")
    if any(t.objection for t in d.turns if t.speaker == "seller"):
        issues.append("objection étiquetée sur un tour du vendeur")
    if d.outcome != spec.outcome:
        issues.append(f"issue {d.outcome} au lieu de {spec.outcome}")
    prospect = [t for t in d.turns if t.speaker == "prospect"]
    if len(prospect) < 3:
        issues.append("trop peu de tours du prospect")
    elif spec.arc[-1] != "neutral":
        last_third = prospect[-max(1, len(prospect) // 3):]
        if not any(t.emotion == spec.arc[-1] for t in last_third):
            issues.append(f"fin d'arc {spec.arc[-1]} absente")
    if not any(t.step == "announcement" for t in d.turns[:4]):
        issues.append("message d'information absent en début d'appel")
    return issues


def parse_response_text(spec: CallSpec, text: str) -> Dialogue:
    data = json.loads(text)
    data["call_id"] = spec.call_id
    return Dialogue.model_validate(data)


def request_params(spec: CallSpec, seller_company: str = "The Herakles") -> dict:
    return {
        "model": DIALOGUE_MODEL,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": DIALOGUE_SCHEMA}},
        "messages": [{"role": "user", "content": build_brief(spec, seller_company)}],
    }


# --- Mode batch -----------------------------------------------------------


def submit_batch(specs: list[CallSpec], seller_company: str = "The Herakles") -> str:
    """Envoie un lot ; retourne l'identifiant du batch. Le repli serveur
    (`fallbacks`) n'est pas accepté par l'API Batches : les refus sont
    rejoués ensuite en mode sync."""
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=[
        Request(custom_id=s.call_id, params=MessageCreateParamsNonStreaming(**request_params(s, seller_company)))
        for s in specs
    ])
    return batch.id


def collect_batch(batch_id: str, specs: list[CallSpec]) -> tuple[dict[str, Dialogue], dict[str, str]]:
    """Récupère un batch terminé. Retourne (dialogues valides, erreurs par call_id)."""
    import anthropic

    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        raise RuntimeError(f"Batch {batch_id} pas terminé ({batch.processing_status})")
    by_id = {s.call_id: s for s in specs}
    ok: dict[str, Dialogue] = {}
    errors: dict[str, str] = {}
    for result in client.messages.batches.results(batch_id):
        spec = by_id.get(result.custom_id)
        if spec is None:
            continue
        if result.result.type != "succeeded":
            errors[spec.call_id] = result.result.type
            continue
        msg = result.result.message
        if msg.stop_reason != "end_turn":
            errors[spec.call_id] = f"stop_reason={msg.stop_reason}"
            continue
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            d = parse_response_text(spec, text)
        except (json.JSONDecodeError, ValidationError) as exc:
            errors[spec.call_id] = f"invalide : {exc}"[:300]
            continue
        issues = validate_dialogue(spec, d)
        if issues:
            errors[spec.call_id] = "; ".join(issues)
        else:
            ok[spec.call_id] = d
    return ok, errors


# --- Mode sync (pilote, rejeu des échecs) -----------------------------------


async def write_one(client, spec: CallSpec, seller_company: str = "The Herakles",
                    attempts: int = 2) -> tuple[Dialogue | None, str | None]:
    import anthropic

    last_error = None
    for _ in range(attempts):
        try:
            msg = await client.beta.messages.create(
                **request_params(spec, seller_company),
                # Repli serveur : en cas de refus, l'API relance sur le modèle adapté.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            await asyncio.sleep(20)
            last_error = "rate_limited"
            continue
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            last_error = f"api: {exc}"[:300]
            continue
        if msg.stop_reason != "end_turn":
            last_error = f"stop_reason={msg.stop_reason}"
            continue
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            d = parse_response_text(spec, text)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = f"invalide : {exc}"[:300]
            continue
        issues = validate_dialogue(spec, d)
        if not issues:
            return d, None
        last_error = "; ".join(issues)
    return None, last_error


async def write_many(specs: list[CallSpec], concurrency: int = 4,
                     seller_company: str = "The Herakles") -> tuple[dict[str, Dialogue], dict[str, str]]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(concurrency)
    ok: dict[str, Dialogue] = {}
    errors: dict[str, str] = {}

    async def run(spec: CallSpec) -> None:
        async with sem:
            d, err = await write_one(client, spec, seller_company)
        if d:
            ok[spec.call_id] = d
        else:
            errors[spec.call_id] = err or "inconnu"
        log.info("%s %s", spec.call_id, "ok" if d else f"échec : {err}")

    await asyncio.gather(*(run(s) for s in specs))
    return ok, errors

