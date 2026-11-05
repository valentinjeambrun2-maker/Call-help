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
    # Analyse émotionnelle de l'utilisateur lui-même (désactivable par l'admin).
    self_analysis: bool = False
    # Variables du scénario : {user_name}, {company}, {topic}...
    context: dict[str, str] = {}


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


class StopMsg(BaseModel):
    type: Literal["stop"] = "stop"


ClientMsg = Annotated[
    StartMsg | TranscriptMsg | VoiceMsg | FaceMsg | FeedbackMsg | PauseAnalysisMsg | StopMsg,
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
    kind: Literal["objection", "step", "pace", "emotion", "question", "closing"]
    text: str  # indication courte
    say: str | None = None  # phrase prête à dire
    source: Literal["rule", "llm"] = "rule"
    priority: int = 1  # 1 = info, 3 = urgent
    replaces: str | None = None  # id d'un conseil par règle remplacé par le LLM
    ts: float = 0.0


class StepState(BaseModel):
    id: str
    title: str
    index: int
    total: int
    goal: str
    active_objection: str | None = None


class StateMsg(BaseModel):
    type: Literal["state"] = "state"
    step: StepState
    metrics: Metrics
    emotions: list[EmotionReading]
    analysis_paused: bool = False


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

    def team_view(self) -> dict:
        return self.model_dump()


class SummaryMsg(BaseModel):
    type: Literal["summary"] = "summary"
    summary: CallSummary
