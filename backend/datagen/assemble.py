"""Assemblage : tours -> ligne de temps, fichier stéréo et étiquettes.

- Vendeur sur le canal gauche, prospect sur le droit (comme dans le produit :
  micro = utilisateur, onglet = interlocuteur).
- Pauses et chevauchements (interruptions) viennent du dialogue.
- Sorties : call.wav, labels.json (vérité terrain), replay.json (rejouable dans
  une session, comme la démo), et une version « téléphone » via ffmpeg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from .dialogue import WORDS_PER_MINUTE, Dialogue

SPEAKER_MAP = {"seller": "user", "prospect": "prospect"}


@dataclass
class TurnAudio:
    """Audio PCM 16 bits mono d'un tour, et alignement mot à mot facultatif."""

    pcm: bytes
    sample_rate: int
    words: list[tuple[str, float, float]] | None = None  # (mot, début, fin) relatifs au tour

    @property
    def duration(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate


class TurnLabel(BaseModel):
    index: int
    speaker: str  # user | prospect (vocabulaire du produit)
    text: str
    start: float
    end: float
    emotion: str
    intensity: float
    delivery: list[str]
    step: str
    objection: str | None
    words: list[tuple[str, float, float]] | None = None  # temps absolus


def estimated_duration(text: str) -> float:
    return max(0.6, len(text.split()) / WORDS_PER_MINUTE * 60)


def timeline(dialogue: Dialogue, durations: list[float]) -> list[tuple[float, float]]:
    """Place chaque tour : fin du précédent + pause, ou avant s'il interrompt."""
    spans: list[tuple[float, float]] = []
    prev_start, prev_end = 0.0, 0.0
    for i, (turn, dur) in enumerate(zip(dialogue.turns, durations, strict=True)):
        if i == 0:
            start = turn.pause_ms / 1000
        elif turn.overlap_ms > 0:
            # Une interruption ne commence jamais avant le début du tour précédent.
            start = max(prev_start + 0.3, prev_end - turn.overlap_ms / 1000)
        else:
            start = prev_end + turn.pause_ms / 1000
        end = start + dur
        spans.append((round(start, 3), round(end, 3)))
        prev_start, prev_end = start, max(prev_end, end)
    return spans


def labels(dialogue: Dialogue, spans: list[tuple[float, float]],
           audios: list[TurnAudio] | None = None) -> list[TurnLabel]:
    out = []
    for i, (turn, (start, end)) in enumerate(zip(dialogue.turns, spans, strict=True)):
        words = None
        if audios and audios[i].words:
            words = [(w, round(start + a, 3), round(start + b, 3)) for w, a, b in audios[i].words]
        out.append(TurnLabel(
            index=i, speaker=SPEAKER_MAP[turn.speaker], text=turn.text, start=start, end=end,
            emotion=turn.emotion, intensity=turn.intensity, delivery=list(turn.delivery),
            step=turn.step, objection=turn.objection, words=words,
        ))
    return out


def replay_events(turn_labels: list[TurnLabel], context: dict[str, str] | None = None) -> dict:
    """Événements au format de la démo (backend/demo), rejouables dans une session.

    Le message d'information est confirmé à la fin du tour « announcement » du
    vendeur. Les émotions ne sont pas injectées : elles serviront à mesurer le
    modèle d'émotion, pas à le court-circuiter.
    """
    events: list[dict] = []
    announced = False
    for t in turn_labels:
        events.append({"type": "transcript", "speaker": t.speaker, "text": t.text,
                       "start": t.start, "end": t.end, "final": True})
        if not announced and t.step == "announcement" and t.speaker == "user":
            events.append({"type": "announcement_done", "ts": t.end})
            announced = True
    events.append({"type": "stop"})
    return {"context": context or {}, "events": events}


def mix_stereo(dialogue: Dialogue, audios: list[TurnAudio],
               spans: list[tuple[float, float]]) -> tuple[bytes, int]:
    rates = {a.sample_rate for a in audios}
    if len(rates) != 1:
        raise ValueError(f"Fréquences d'échantillonnage différentes : {rates}")
    sr = rates.pop()
    total = int(max(e for _, e in spans) * sr) + sr // 2
    left, right = array("h", bytes(total * 2)), array("h", bytes(total * 2))
    for turn, audio, (start, _) in zip(dialogue.turns, audios, spans, strict=True):
        channel = left if turn.speaker == "seller" else right
        samples = array("h")
        samples.frombytes(audio.pcm)
        offset = int(start * sr)
        for j, v in enumerate(samples):
            k = offset + j
            if k >= total:
                break
            channel[k] = max(-32768, min(32767, channel[k] + v))
    stereo = array("h", bytes(total * 4))
    stereo[0::2] = left
    stereo[1::2] = right
    return stereo.tobytes(), sr


def write_wav(path: Path, pcm: bytes, sample_rate: int, channels: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def telephone_version(src: Path, dst: Path) -> bool:
    """Bande téléphonique 300-3400 Hz, 8 kHz, passée par le codec G.711 µ-law puis
    redécodée en PCM 16 bits : les artefacts du codec restent, le fichier se lit
    partout. Canaux conservés."""
    if not shutil.which("ffmpeg"):
        return False
    ulaw = dst.with_suffix(".ulaw.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-af", "highpass=f=300,lowpass=f=3400", "-ar", "8000", "-c:a", "pcm_mulaw", str(ulaw)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ulaw), "-c:a", "pcm_s16le", str(dst)],
            check=True,
        )
    finally:
        ulaw.unlink(missing_ok=True)
    return True


def write_call(out_dir: Path, dialogue: Dialogue, audios: list[TurnAudio] | None,
               context: dict[str, str] | None = None, telephone: bool = True) -> list[TurnLabel]:
    """Écrit un appel complet. Sans audio, la ligne de temps est estimée (150 mots/min)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    durations = [a.duration for a in audios] if audios else [estimated_duration(t.text) for t in dialogue.turns]
    spans = timeline(dialogue, durations)
    turn_labels = labels(dialogue, spans, audios)
    (out_dir / "dialogue.json").write_text(dialogue.model_dump_json(indent=1), encoding="utf-8")
    (out_dir / "labels.json").write_text(
        json.dumps([t.model_dump() for t in turn_labels], ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "replay.json").write_text(
        json.dumps(replay_events(turn_labels, context), ensure_ascii=False, indent=1), encoding="utf-8")
    if audios:
        pcm, sr = mix_stereo(dialogue, audios, spans)
        write_wav(out_dir / "call.wav", pcm, sr, channels=2)
        if telephone:
            telephone_version(out_dir / "call.wav", out_dir / "call_telephone.wav")
    return turn_labels
