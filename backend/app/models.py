"""Schémas partagés : messages WebSocket, conseils, émotions."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Speaker(str, Enum):
    USER = "user"
    PROSPECT = "prospect"


class EmotionState(str, Enum):
    INTEREST = "interest"
    HESITATION = "hesitation"
    ANNOYANCE = "annoyance"
    BOREDOM = "boredom"
    ENTHUSIASM = "enthusiasm"


# --- Messages client -> serveur -------------------------------------------


class StartMsg(BaseModel):
    type: Literal["start"] = "start"
    scenario_id: str = "cold_call_fr"
    language: Literal["fr", "en"] = "fr"
    # Analyse du ton de l'utilisateur lui-même : sur accord explicite (opt-in), choix
    # mémorisé par le panneau. Jamais partagée avec un manager ou un admin.
    self_analysis: bool = False
    # Variables du scénario : {user_name}, {company}, {topic}...
    context: dict[str, str] = {}
    # "client" : le client envoie la transcription (rejeu, démo de référence).
    # "server" : le client envoie l'audio (trames binaires), le backend transcrit en direct.
    transcription: Literal["client", "server"] = "client"
    # Noms propres pour aider la transcription (prospect, entreprise, offre).
    keyterms: list[str] = []


class TranscriptMsg(BaseModel):
    type: Literal["transcript"] = "transcript"
    speaker: Speaker
    text: str
    start: float = Field(ge=0, description="Secondes depuis le début de l'appel")
    end: float = Field(ge=0)
    final: bool = True


class VoiceMsg(BaseModel):
    """Scores d'émotion vocale produits par un fournisseur (externe ou local)."""

    type: Literal["voice"] = "voice"
    speaker: Speaker
    ts: float
    scores: dict[EmotionState, float]


class FaceMsg(BaseModel):
    """Scores d'expressions calculés DANS le navigateur. Aucune image ne transite."""

    type: Literal["face"] = "face"
    speaker: Speaker
    ts: float
    visible: bool = True
    smile: float = Field(0, ge=0, le=1)
    frown: float = Field(0, ge=0, le=1)
    nod: float = Field(0, ge=0, le=1)
    gaze_away: float = Field(0, ge=0, le=1)


class FeedbackMsg(BaseModel):
    type: Literal["feedback"] = "feedback"
    hint_id: str
    useful: bool


class PauseAnalysisMsg(BaseModel):
    """Bouton « couper l'analyse » : arrête voix + visage pour le reste de l'appel."""

    type: Literal["pause_analysis"] = "pause_analysis"


class AnnouncementDoneMsg(BaseModel):
    """Le message d'information a été dit ; `consent` = réponse du prospect.
    Sans accord, aucune analyse émotionnelle pendant tout l'appel."""

    type: Literal["announcement_done"] = "announcement_done"
    ts: float = Field(0, ge=0)
    consent: bool


class StopMsg(BaseModel):
    type: Literal["stop"] = "stop"


ClientMsg = Annotated[
    StartMsg | TranscriptMsg | VoiceMsg | FaceMsg | FeedbackMsg | PauseAnalysisMsg
    | AnnouncementDoneMsg | StopMsg,
    Field(discriminator="type"),
]


# --- Serveur -> client ----------------------------------------------------


class EmotionReading(BaseModel):
    speaker: Speaker
    state: EmotionState | None  # None = rien d'affichable
    confidence: float = 0.0
    label: Literal["sûr", "probable", "incertain"] = "incertain"
    face_visible: bool | None = None
    calibrated: bool = False


class Metrics(BaseModel):
    elapsed: float = 0.0
    user_wpm: float = 0.0
    prospect_wpm: float = 0.0
    user_talk_ratio: float = 0.0
    current_monologue: float = 0.0  # durée de parole continue de l'utilisateur
    questions_asked: int = 0


class Hint(BaseModel):
    id: str
    kind: Literal["announcement", "objection", "step", "pace", "emotion", "question", "closing"]
    text: str  # indication courte
    say: str | None = None  # phrase prête à dire
    source: Literal["rule", "llm"] = "rule"
    priority: int = 1  # 1 = info, 3 = urgent
    replaces: str | None = None  # id d'un conseil par règle remplacé par le LLM
    ref: str | None = None  # objection ou étape concernée (évaluation, affichage)
    ts: float = 0.0


class StepState(BaseModel):
    id: str
    title: str
    index: int
    total: int
    goal: str
    active_objection: str | None = None


class TranscriptEcho(BaseModel):
    """Transcription produite par le backend, renvoyée au client pour affichage."""

    type: Literal["transcript"] = "transcript"
    speaker: Speaker
    text: str
    start: float
    end: float
    final: bool


class StateMsg(BaseModel):
    type: Literal["state"] = "state"
    step: StepState
    metrics: Metrics
    emotions: list[EmotionReading]
    analysis_paused: bool = False
    # Vrai tant que le message d'information n'a pas été dit : l'analyse
    # émotionnelle (voix + visage) ne démarre qu'après.
    announcement_pending: bool = False
    prospect_consent: bool | None = None


class HintMsg(BaseModel):
    type: Literal["hint"] = "hint"
    hint: Hint


class CallSummary(BaseModel):
    """Bilan de fin d'appel. `team_view` ne contient jamais d'émotions (Q38)."""

    duration: float
    steps_reached: list[str]
    objections: list[str]
    metrics: Metrics
    hints_given: int
    hints_useful: int
    hints_not_useful: int
    # Trace de conformité : moment du message d'information, réponse du prospect,
    # et analyse émotionnelle réellement effectuée ou non.
    announcement_at: float | None = None
    prospect_consent: bool | None = None
    emotion_analysis_used: bool = False
    transcription_cost_usd: float | None = None

    def team_view(self) -> dict:
        return self.model_dump()


class SummaryMsg(BaseModel):
    type: Literal["summary"] = "summary"
    summary: CallSummary
