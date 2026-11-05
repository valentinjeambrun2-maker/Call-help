"""Fusion des indices émotionnels voix + visage, avec calibration et seuils.

Principes (docs/decisions.md) :
- l'analyse ne démarre qu'une fois le message d'information dit (`start`) ;
- un indice est une hypothèse, jamais un fait : il porte toujours une confiance ;
- rien n'est affiché avant la fin de la calibration (30 s après le premier
  échantillon de chaque canal) ; un canal non calibré est ignoré ;
- les échantillons horodatés avant le message d'information sont rejetés ;
- >= 70 % : affiché ; 50-70 % : affiché avec le libellé « probable » ; < 50 % : rien.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import EmotionReading, EmotionState, FaceMsg, Speaker

CALIBRATION_SECONDS = 30.0
SHOW_THRESHOLD = 0.70
PROBABLE_THRESHOLD = 0.50
SMOOTHING = 0.4  # poids de la nouvelle mesure dans la moyenne glissante
# Poids des canaux dans la fusion (renormalisés sur les canaux disponibles).
WEIGHTS = {"voice": 0.5, "face": 0.3, "text": 0.4}
TEXT_SMOOTHING = 0.6  # le texte arrive tour par tour : on suit plus vite le dernier tour

STATES = list(EmotionState)


def face_to_scores(face: FaceMsg) -> dict[EmotionState, float]:
    """Traduit les expressions (calculées dans le navigateur) en indices d'état."""
    return {
        EmotionState.INTEREST: min(1.0, 0.6 * face.nod + 0.4 * (1 - face.gaze_away)),
        EmotionState.ENTHUSIASM: face.smile,
        EmotionState.ANNOYANCE: face.frown,
        EmotionState.HESITATION: min(1.0, 0.5 * face.frown + 0.5 * face.gaze_away),
        EmotionState.BOREDOM: face.gaze_away * (1 - face.nod),
    }


def _ema(old: dict[EmotionState, float], new: dict[EmotionState, float]) -> dict:
    return {s: (1 - SMOOTHING) * old.get(s, 0.0) + SMOOTHING * new.get(s, 0.0) for s in STATES}


@dataclass
class _Channel:
    """Un canal (voix ou visage) d'une personne. Sa calibration démarre à son
    premier échantillon : une caméra allumée tard se calibre à son tour."""

    current: dict[EmotionState, float] = field(default_factory=dict)
    baseline_sum: dict[EmotionState, float] = field(default_factory=dict)
    baseline_n: int = 0
    since: float | None = None

    def update(self, scores: dict[EmotionState, float], ts: float) -> None:
        if self.since is None:
            self.since = ts
        self.current = _ema(self.current, scores) if self.current else dict(scores)
        if ts < self.since + CALIBRATION_SECONDS:
            for s in STATES:
                self.baseline_sum[s] = self.baseline_sum.get(s, 0.0) + scores.get(s, 0.0)
            self.baseline_n += 1

    def calibrated(self, now: float) -> bool:
        return self.since is not None and self.baseline_n > 0 and now >= self.since + CALIBRATION_SECONDS

    def relative(self, now: float) -> dict[EmotionState, float]:
        """Écart à la base de la personne : une voix grave n'est pas « agacée ».
        Vide tant que le canal n'est pas calibré (jamais de scores bruts)."""
        if not self.current or not self.calibrated(now):
            return {}
        out = {}
        for s in STATES:
            base = self.baseline_sum.get(s, 0.0) / self.baseline_n
            value = self.current.get(s, 0.0)
            out[s] = max(0.0, (value - base) / (1 - base)) if base < 1 else 0.0
        return out


@dataclass
class _TextChannel:
    """État lu dans les mots du prospect (estimé par le LLM, déjà relatif au neutre) :
    pas de calibration, lissage d'un tour à l'autre."""

    current: dict[EmotionState, float] = field(default_factory=dict)

    def update(self, state: EmotionState | None, confidence: float) -> None:
        scores = {s: 0.0 for s in STATES}
        if state is not None:
            scores[state] = max(0.0, min(1.0, confidence))
        if not self.current:
            self.current = scores
        else:
            self.current = {s: (1 - TEXT_SMOOTHING) * self.current.get(s, 0.0) + TEXT_SMOOTHING * scores[s]
                            for s in STATES}

    def relative(self, now: float) -> dict[EmotionState, float]:
        return dict(self.current)


@dataclass
class _Person:
    voice: _Channel = field(default_factory=_Channel)
    face: _Channel = field(default_factory=_Channel)
    text: _TextChannel = field(default_factory=_TextChannel)
    face_visible: bool | None = None


class EmotionFusion:
    def __init__(self) -> None:
        self.people = {sp: _Person() for sp in Speaker}
        self.paused = False
        self.active_since: float | None = None  # None = analyse pas encore démarrée
        self.samples_used = 0  # trace de conformité : échantillons réellement analysés
        self.blocked: set[Speaker] = set()  # personnes ayant refusé l'analyse

    def start(self, ts: float) -> None:
        """Démarre l'analyse (après le message d'information). Idempotent."""
        if self.active_since is None:
            self.active_since = ts

    @property
    def running(self) -> bool:
        return self.active_since is not None and not self.paused

    def _accepts(self, ts: float, speaker: Speaker) -> bool:
        """Rien d'antérieur au message d'information, rien pendant une coupure,
        rien pour une personne qui a refusé."""
        return self.running and speaker not in self.blocked and ts >= self.active_since  # type: ignore[operator]

    def add_voice(self, speaker: Speaker, scores: dict[EmotionState, float], ts: float) -> None:
        if self._accepts(ts, speaker):
            self.people[speaker].voice.update(scores, ts)
            self.samples_used += 1

    def add_face(self, face: FaceMsg) -> None:
        if not self._accepts(face.ts, face.speaker):
            return
        person = self.people[face.speaker]
        person.face_visible = face.visible
        if face.visible:
            person.face.update(face_to_scores(face), face.ts)
            self.samples_used += 1

    def add_text(self, speaker: Speaker, state: EmotionState | None, confidence: float, ts: float) -> None:
        """État lu dans les mots (classement du LLM). Mêmes garde-fous que la voix et le visage."""
        if self._accepts(ts, speaker):
            self.people[speaker].text.update(state, confidence)
            self.samples_used += 1

    def reading(self, speaker: Speaker, now: float) -> EmotionReading:
        person = self.people[speaker]
        calibrated = person.voice.calibrated(now) or person.face.calibrated(now) or bool(person.text.current)
        empty = EmotionReading(
            speaker=speaker, state=None, face_visible=person.face_visible, calibrated=calibrated
        )
        if not self.running or speaker in self.blocked or not calibrated:
            return empty

        channels = {
            "voice": person.voice.relative(now),
            "face": person.face.relative(now) if person.face_visible else {},
            "text": person.text.relative(now),
        }
        channels = {k: v for k, v in channels.items() if v}
        if not channels:
            return empty
        total = sum(WEIGHTS[k] for k in channels)
        fused = {s: sum(WEIGHTS[k] * v.get(s, 0.0) for k, v in channels.items()) / total for s in STATES}

        state, confidence = max(fused.items(), key=lambda kv: kv[1])
        if confidence < PROBABLE_THRESHOLD:
            return empty.model_copy(update={"confidence": round(confidence, 2)})
        return EmotionReading(
            speaker=speaker,
            state=state,
            confidence=round(confidence, 2),
            label="sûr" if confidence >= SHOW_THRESHOLD else "probable",
            face_visible=person.face_visible,
            calibrated=True,
        )
