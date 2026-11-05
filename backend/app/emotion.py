"""Fusion des indices émotionnels voix + visage, avec calibration et seuils.

Principes (docs/decisions.md) :
- l'analyse ne démarre qu'une fois le message d'information dit (`start`) ;
- un indice est une hypothèse, jamais un fait : il porte toujours une confiance ;
- rien n'est affiché avant la fin de la calibration (30 s après le démarrage) ;
- >= 70 % : affiché ; 50-70 % : affiché avec le libellé « probable » ; < 50 % : rien.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import EmotionReading, EmotionState, FaceMsg, Speaker

CALIBRATION_SECONDS = 30.0
SHOW_THRESHOLD = 0.70
PROBABLE_THRESHOLD = 0.50
SMOOTHING = 0.4  # poids de la nouvelle mesure dans la moyenne glissante
VOICE_WEIGHT = 0.6
FACE_WEIGHT = 0.4

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
    current: dict[EmotionState, float] = field(default_factory=dict)
    baseline_sum: dict[EmotionState, float] = field(default_factory=dict)
    baseline_n: int = 0

    def update(self, scores: dict[EmotionState, float], calibrating: bool) -> None:
        self.current = _ema(self.current, scores) if self.current else dict(scores)
        if calibrating:
            for s in STATES:
                self.baseline_sum[s] = self.baseline_sum.get(s, 0.0) + scores.get(s, 0.0)
            self.baseline_n += 1

    def relative(self) -> dict[EmotionState, float]:
        """Écart à la base de la personne : une voix grave n'est pas « agacée »."""
        if not self.current:
            return {}
        out = {}
        for s in STATES:
            base = self.baseline_sum.get(s, 0.0) / self.baseline_n if self.baseline_n else 0.0
            value = self.current.get(s, 0.0)
            out[s] = max(0.0, (value - base) / (1 - base)) if base < 1 else 0.0
        return out


@dataclass
class _Person:
    voice: _Channel = field(default_factory=_Channel)
    face: _Channel = field(default_factory=_Channel)
    face_visible: bool | None = None


class EmotionFusion:
    def __init__(self) -> None:
        self.people = {sp: _Person() for sp in Speaker}
        self.paused = False
        self.active_since: float | None = None  # None = analyse pas encore démarrée

    def start(self, ts: float) -> None:
        """Démarre l'analyse (après le message d'information). Idempotent."""
        if self.active_since is None:
            self.active_since = ts

    @property
    def running(self) -> bool:
        return self.active_since is not None and not self.paused

    def _calibrating(self, ts: float) -> bool:
        return self.active_since is not None and ts < self.active_since + CALIBRATION_SECONDS

    def add_voice(self, speaker: Speaker, scores: dict[EmotionState, float], ts: float) -> None:
        if self.running:
            self.people[speaker].voice.update(scores, self._calibrating(ts))

    def add_face(self, face: FaceMsg) -> None:
        if not self.running:
            return
        person = self.people[face.speaker]
        person.face_visible = face.visible
        if face.visible:
            person.face.update(face_to_scores(face), self._calibrating(face.ts))

    def reading(self, speaker: Speaker, now: float) -> EmotionReading:
        person = self.people[speaker]
        calibrated = self.active_since is not None and not self._calibrating(now)
        empty = EmotionReading(
            speaker=speaker, state=None, face_visible=person.face_visible, calibrated=calibrated
        )
        if not self.running or not calibrated:
            return empty

        voice = person.voice.relative()
        face = person.face.relative() if person.face_visible else {}
        if not voice and not face:
            return empty

        if voice and face:
            fused = {s: VOICE_WEIGHT * voice[s] + FACE_WEIGHT * face[s] for s in STATES}
        else:
            fused = voice or face

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
