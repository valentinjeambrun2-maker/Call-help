"""Synthèse vocale ElevenLabs, tour par tour.

Choix (voir docs/donnees-synthetiques.md) :
- un appel TTS par tour plutôt que l'API Text to Dialogue : celle-ci rend une
  seule piste mono mélangée (impossible de séparer vendeur et prospect) et ses
  horodatages dérivent au-delà de 30-60 s (bogues ouverts côté API) ;
- nous plaçons nous-mêmes les tours (datagen/assemble.py) : la ligne de temps de
  référence est exacte, quelle que soit la qualité de l'alignement renvoyé ;
- émotion pilotée par balises de jeu en tête de tour ([hesitant], [sighs]...) et
  par la stabilité (plus basse = plus expressif) ;
- voix réparties par lot (train / val / test) : aucune voix de test n'est vue à
  l'entraînement.

Conditions d'utilisation : la politique d'usage d'ElevenLabs interdit d'utiliser
les sorties pour entraîner un modèle d'IA sans autorisation expresse. Chaque appel
porte donc une provenance explicite (provenance.json).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import re
import wave
from pathlib import Path

from pydantic import BaseModel

from .assemble import TurnAudio
from .dialogue import Dialogue, Turn
from .plan import CallSpec

log = logging.getLogger(__name__)

VOICE_MODEL = os.getenv("CALLHELP_VOICE_MODEL", "eleven_v4")
SAMPLE_RATE = 16000
OUTPUT_FORMAT = f"pcm_{SAMPLE_RATE}"
VOICES_FILE = Path(os.getenv("CALLHELP_VOICES_FILE", Path(__file__).with_name("voices.json")))

# Indications de jeu -> balises ElevenLabs (texte libre entre crochets).
TAGS = {
    "hesitant": "[hesitant]", "annoyed": "[annoyed]", "excited": "[excited]", "bored": "[bored]",
    "curious": "[curious]", "warm": "[warmly]", "rushed": "[quickly]", "calm": "[calmly]",
    "thoughtful": "[thoughtful]", "skeptical": "[skeptical]", "sighs": "[sighs]",
    "laughs": "[laughs]", "clears_throat": "[clears throat]", "interrupting": "[interrupting]",
}
# Balise de repli quand l'émotion est marquée mais qu'aucune indication n'est donnée.
EMOTION_TAGS = {
    "hesitation": "[hesitant]", "annoyance": "[annoyed]", "boredom": "[bored]",
    "enthusiasm": "[excited]", "interest": "[curious]",
}
TAG_RE = re.compile(r"\[[^\]]*\]")


class Voice(BaseModel):
    voice_id: str
    name: str = ""
    split: str = "train"  # train | val | test


class VoiceBook(BaseModel):
    """voices.json : {"fr": {"female": [...], "male": [...]}, "en": {...}}"""

    model_config = {"extra": "ignore"}

    fr: dict[str, list[Voice]] = {}
    en: dict[str, list[Voice]] = {}

    @classmethod
    def load(cls, path: Path = VOICES_FILE) -> "VoiceBook":
        if not path.exists():
            raise SystemExit(f"Fichier de voix absent : {path}. Copiez voices.example.json et "
                             "renseignez des voice_id ElevenLabs (voir docs/donnees-synthetiques.md).")
        return cls.model_validate_json(path.read_text("utf-8"))

    def pick(self, language: str, gender: str, split: str, seed: int, exclude: str | None = None) -> Voice:
        pool = getattr(self, language).get(gender, [])
        candidates = [v for v in pool if v.split == split and v.voice_id != exclude]
        if not candidates:
            raise ValueError(f"Aucune voix {language}/{gender} pour le lot « {split} » : "
                             "ajoutez-en dans voices.json (sinon fuite de locuteur entre lots).")
        return random.Random(seed).choice(candidates)


def render_text(turn: Turn) -> str:
    """Texte envoyé à ElevenLabs : balises de jeu en tête, puis la réplique."""
    tags = [TAGS[d] for d in turn.delivery if d in TAGS]
    if not tags and turn.emotion in EMOTION_TAGS and turn.intensity >= 0.5:
        tags = [EMOTION_TAGS[turn.emotion]]
    return (" ".join(tags) + " " + turn.text).strip() if tags else turn.text


def stability(turn: Turn) -> float:
    """Plus l'émotion est intense, plus la voix doit être libre (stabilité basse)."""
    if turn.emotion == "neutral":
        return 0.6
    return round(min(0.6, max(0.3, 0.6 - 0.3 * turn.intensity)), 2)


def estimate_characters(dialogue: Dialogue) -> int:
    return sum(len(render_text(t)) for t in dialogue.turns)


def words_from_alignment(chars: list[str], starts: list[float], ends: list[float]) -> list[tuple[str, float, float]]:
    """Alignement caractère par caractère -> mots horodatés, balises [..] retirées."""
    words: list[tuple[str, float, float]] = []
    current, w_start, w_end, in_tag = "", 0.0, 0.0, False
    for c, s, e in zip(chars, starts, ends, strict=False):
        if c == "[":
            in_tag = True
        if in_tag:
            if c == "]":
                in_tag = False
            continue
        if c.isspace():
            if current:
                words.append((current, round(w_start, 3), round(w_end, 3)))
            current = ""
            continue
        if not current:
            w_start = s
        current += c
        w_end = e
    if current:
        words.append((current, round(w_start, 3), round(w_end, 3)))
    return words


def assign_voices(spec: CallSpec, book: VoiceBook) -> dict[str, Voice]:
    seller = book.pick(spec.language, spec.seller_gender, spec.split, spec.seed)
    prospect = book.pick(spec.language, spec.prospect_gender, spec.split, spec.seed + 1, exclude=seller.voice_id)
    return {"seller": seller, "prospect": prospect}


def _tts_kwargs(turn: Turn, voice: Voice, language: str, seed: int, model: str,
                previous_text: str | None) -> dict:
    from elevenlabs import VoiceSettings

    kwargs = dict(
        text=render_text(turn),
        model_id=model,
        language_code=language,
        output_format=OUTPUT_FORMAT,
        voice_settings=VoiceSettings(stability=stability(turn), similarity_boost=0.75),
        seed=seed,
        request_options={"max_retries": 6},  # le SDK réessaie 429 / 5xx sur cet appel
    )
    # Le SDK envoie `null` pour un None explicite : on n'ajoute le champ que s'il existe.
    if previous_text:
        kwargs["previous_text"] = previous_text[-300:]
    return kwargs


def _write_mono_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


async def synthesize_call(client, call_dir: Path, dialogue: Dialogue, spec: CallSpec,
                          voices: dict[str, Voice], model: str, sem: asyncio.Semaphore) -> None:
    turns_dir = call_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict | None] = [None] * len(dialogue.turns)

    async def one(i: int, turn: Turn) -> None:
        wav = turns_dir / f"{i:03d}.wav"
        if wav.exists():
            return
        voice = voices[turn.speaker]
        previous = dialogue.turns[i - 1].text if i else None
        kwargs = _tts_kwargs(turn, voice, spec.language, spec.seed * 1000 + i, model, previous)
        async with sem:
            raw = await client.text_to_speech.with_raw_response.convert_with_timestamps(voice.voice_id, **kwargs)
        res = raw.data
        pcm = base64.b64decode(res.audio_base_64)
        words = None
        if res.alignment:
            a = res.alignment
            words = words_from_alignment(a.characters, a.character_start_times_seconds,
                                         a.character_end_times_seconds)
        _write_mono_wav(wav, pcm)
        meta[i] = {
            "index": i, "speaker": turn.speaker, "voice_id": voice.voice_id, "model": model,
            "text_sent": kwargs["text"], "stability": stability(turn), "seed": kwargs["seed"],
            "request_id": raw.headers.get("request-id"), "character_cost": raw.headers.get("character-cost"),
            "words": words,
        }

    await asyncio.gather(*(one(i, t) for i, t in enumerate(dialogue.turns)))
    previous = json.loads((call_dir / "turns.json").read_text("utf-8")) if (call_dir / "turns.json").exists() else []
    merged = [m or (previous[i] if i < len(previous) else None) for i, m in enumerate(meta)]
    (call_dir / "turns.json").write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    (call_dir / "provenance.json").write_text(json.dumps({
        "source": "synthetic", "dialogue_model": "anthropic", "voice_provider": "elevenlabs",
        "voice_model": model, "voices": {k: v.voice_id for k, v in voices.items()}, "split": spec.split,
        "terms_note": "Sorties ElevenLabs : usage pour entraîner un modèle d'IA interdit sans autorisation "
                      "expresse (Prohibited Use Policy). Réservé aux tests et à l'évaluation.",
    }, ensure_ascii=False, indent=1), encoding="utf-8")


async def synthesize_many(todo: list[tuple[Path, Dialogue]], specs: dict[str, CallSpec],
                          concurrency: int = 2, model: str | None = None) -> None:
    from elevenlabs import AsyncElevenLabs
    from elevenlabs.core import ApiError

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY absent.")
    # Clé passée explicitement : le SDK lit sinon la variable au moment de l'import.
    client = AsyncElevenLabs(api_key=key)
    book = VoiceBook.load()
    sem = asyncio.Semaphore(concurrency)  # limite de requêtes simultanées du forfait
    for call_dir, dialogue in todo:
        spec = specs[dialogue.call_id]
        try:
            voices = assign_voices(spec, book)
            await synthesize_call(client, call_dir, dialogue, spec, voices, model or VOICE_MODEL, sem)
            log.info("%s : %d tours synthétisés", dialogue.call_id, len(dialogue.turns))
        except ApiError as exc:
            log.error("%s : erreur ElevenLabs %s %s", dialogue.call_id, exc.status_code, exc.body)
            if exc.status_code in (401, 402, 403):  # clé, quota ou droits : inutile de continuer
                raise SystemExit(f"Arrêt : ElevenLabs a répondu {exc.status_code}") from exc
        except ValueError as exc:
            log.error("%s : %s", dialogue.call_id, exc)


def load_turn_audios(call_dir: Path) -> list[TurnAudio] | None:
    """Audio des tours s'ils existent tous, sinon None (ligne de temps estimée)."""
    meta_path = call_dir / "turns.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text("utf-8"))
    audios = []
    for i, m in enumerate(meta):
        wav = call_dir / "turns" / f"{i:03d}.wav"
        if m is None or not wav.exists():
            return None
        with wave.open(str(wav)) as w:
            pcm, sr = w.readframes(w.getnframes()), w.getframerate()
        words = [tuple(x) for x in m["words"]] if m.get("words") else None
        audios.append(TurnAudio(pcm, sr, words))
    return audios
