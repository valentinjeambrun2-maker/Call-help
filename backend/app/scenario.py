"""Scénario d'appel : étapes, branches d'objection, suivi de progression."""

from __future__ import annotations

import json
import re
import string
import unicodedata
from pathlib import Path

from pydantic import BaseModel

from .models import StepState

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"


class Step(BaseModel):
    id: str
    title: str
    goal: str
    max_duration: float
    exit_keywords: list[str] = []
    say: dict[str, str] = {}


class Objection(BaseModel):
    id: str
    label: str
    keywords: list[str]
    hint: str
    say: dict[str, str] = {}


class Scenario(BaseModel):
    id: str
    title: str
    target_duration: float
    # Message d'information dit au prospect avant l'analyse (cold call : pas
    # d'invitation pour l'informer). None = information déjà faite (invitation).
    announcement: dict[str, str] | None = None
    steps: list[Step]
    objections: list[Objection] = []


def load_scenario(scenario_id: str, directory: Path = SCENARIOS_DIR) -> Scenario:
    if not re.fullmatch(r"[a-z0-9_]+", scenario_id):
        raise ValueError(f"Identifiant de scénario invalide : {scenario_id!r}")
    path = directory / f"{scenario_id}.json"
    return Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))


def list_scenarios(directory: Path = SCENARIOS_DIR) -> list[dict]:
    out = []
    for path in sorted(directory.glob("*.json")):
        sc = Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
        out.append({"id": sc.id, "title": sc.title, "steps": len(sc.steps)})
    return out


def normalize(text: str) -> str:
    """Minuscules, sans accents, apostrophes unifiées : « Déjà » == « deja »."""
    text = text.lower().replace("’", "'")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def contains_keyword(text: str, keyword: str) -> bool:
    """Correspondance sur des mots entiers, pour que « ok » ne matche pas « book ».
    À gauche, l'apostrophe sépare les mots (élision : « qu'on cherche »)."""
    pattern = r"(?<!\w)" + re.escape(normalize(keyword)) + r"(?![\w'])"
    return re.search(pattern, normalize(text)) is not None


# Variables non renseignées : affichées comme un champ à compléter, jamais en brut.
PLACEHOLDERS = {
    "fr": {"user_name": "votre prénom", "company": "votre entreprise", "segment": "type de clients",
           "benefit": "bénéfice principal", "topic": "sujet", "client_ref": "client de référence",
           "result": "résultat obtenu"},
    "en": {"user_name": "your name", "company": "your company", "segment": "type of clients",
           "benefit": "main benefit", "topic": "topic", "client_ref": "reference client",
           "result": "result achieved"},
}


class _SafeDict(dict):
    def __init__(self, values: dict[str, str], language: str) -> None:
        super().__init__(values)
        self.labels = PLACEHOLDERS.get(language, PLACEHOLDERS["fr"])

    def __missing__(self, key: str) -> str:
        return "[" + self.labels.get(key, key) + "]"


def fill(template: str | None, context: dict[str, str], language: str = "fr") -> str | None:
    """Remplit les variables connues ({company}...) ; les autres deviennent [votre entreprise]."""
    if template is None:
        return None
    values = {k: v for k, v in context.items() if v}
    return string.Formatter().vformat(template, (), _SafeDict(values, language))


class ScenarioTracker:
    """Suit l'étape courante et détecte les objections dans la parole du prospect."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.index = 0
        self.step_started_at = 0.0
        self.active_objection: Objection | None = None
        self.objections_seen: list[str] = []
        self.steps_reached: list[str] = [scenario.steps[0].id]

    @property
    def step(self) -> Step:
        return self.scenario.steps[self.index]

    def detect_objection(self, text: str) -> Objection | None:
        for obj in self.scenario.objections:
            if any(contains_keyword(text, kw) for kw in obj.keywords):
                return obj
        return None

    def on_prospect_text(self, text: str, ts: float) -> tuple[Objection | None, bool]:
        """Retourne (objection détectée, étape avancée)."""
        objection = self.detect_objection(text)
        if objection:
            self.active_objection = objection
            if objection.id not in self.objections_seen:
                self.objections_seen.append(objection.id)
            return objection, False

        self.active_objection = None
        if any(contains_keyword(text, kw) for kw in self.step.exit_keywords):
            return None, self.advance(ts)
        return None, False

    def advance(self, ts: float) -> bool:
        if self.index >= len(self.scenario.steps) - 1:
            return False
        self.index += 1
        self.step_started_at = ts
        self.steps_reached.append(self.step.id)
        return True

    def step_overtime(self, now: float) -> bool:
        return now - self.step_started_at > self.step.max_duration

    def state(self) -> StepState:
        return StepState(
            id=self.step.id,
            title=self.step.title,
            index=self.index,
            total=len(self.scenario.steps),
            goal=self.step.goal,
            active_objection=self.active_objection.label if self.active_objection else None,
        )
