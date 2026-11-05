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
    model_config = {"extra": "ignore"}

    voice_id: str
    name: str = ""
    split: str = "train"  # train | val | test


class VoiceBook(BaseModel):
    """voices.json : {"fr": {"female": [...], "male": [...]}, "en": {...}}"""

    model_config = {"extra": "ignore"}

    fr: dict[str, list[Voice]] = {}
    en: dict[str, list[Voice]] = {}

    @classmethod
    def load(cls, path: Path | None = None) -> "VoiceBook":
        path = path or VOICES_FILE
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


def tts_seed(spec: CallSpec, index: int) -> int:
    """Graine ElevenLabs dans sa plage documentée [0, 2**32 - 1], quelle que soit la graine du plan."""
    return (spec.seed * 1000 + index) % 2**32


def _turn_sidecar(turns_dir: Path, i: int) -> Path:
    return turns_dir / f"{i:03d}.json"


def _done_turn(turns_dir: Path, i: int, turn: Turn) -> dict | None:
    """Métadonnées d'un tour déjà synthétisé pour CE texte, sinon None (à refaire)."""
    wav, side = turns_dir / f"{i:03d}.wav", _turn_sidecar(turns_dir, i)
    if not (wav.exists() and side.exists()):
        return None
    meta = json.loads(side.read_text("utf-8"))
    return meta if meta.get("text_sent") == render_text(turn) else None


async def synthesize_call(client, call_dir: Path, dialogue: Dialogue, spec: CallSpec,
                          voices: dict[str, Voice], model: str, sem: asyncio.Semaphore) -> None:
    """Synthétise les tours manquants. Chaque tour écrit son WAV et ses métadonnées :
    une interruption ne perd rien, et turns.json n'est écrit que si tout est là."""
    turns_dir = call_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict | None] = [_done_turn(turns_dir, i, t) for i, t in enumerate(dialogue.turns)]

    async def one(i: int, turn: Turn) -> None:
        if meta[i] is not None:
            return
        voice = voices[turn.speaker]
        previous = dialogue.turns[i - 1].text if i else None
        kwargs = _tts_kwargs(turn, voice, spec.language, tts_seed(spec, i), model, previous)
        async with sem:
            raw = await client.text_to_speech.with_raw_response.convert_with_timestamps(voice.voice_id, **kwargs)
        res = raw.data
        pcm = base64.b64decode(res.audio_base_64)
        words = None
        if res.alignment:
            a = res.alignment
            words = words_from_alignment(a.characters, a.character_start_times_seconds,
                                         a.character_end_times_seconds)
        m = {
            "index": i, "speaker": turn.speaker, "voice_id": voice.voice_id, "model": model,
            "text_sent": kwargs["text"], "stability": stability(turn), "seed": kwargs["seed"],
            "request_id": raw.headers.get("request-id"), "character_cost": raw.headers.get("character-cost"),
            "words": words,
        }
        _write_mono_wav(turns_dir / f"{i:03d}.wav", pcm)
        _turn_sidecar(turns_dir, i).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        meta[i] = m

    results = await asyncio.gather(*(one(i, t) for i, t in enumerate(dialogue.turns)), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    if errors:
        raise errors[0]  # les tours réussis sont conservés ; la relance reprendra les autres
    (call_dir / "turns.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
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


def audio_complete(call_dir: Path, dialogue: Dialogue) -> bool:
    """Vrai si chaque tour a son audio pour le texte actuel (métadonnées seules, rapide)."""
    meta_path = call_dir / "turns.json"
    if not meta_path.exists():
        return False
    meta = json.loads(meta_path.read_text("utf-8"))
    return len(meta) == len(dialogue.turns) and all(
        m and m.get("text_sent") == render_text(t) and (call_dir / "turns" / f"{i:03d}.wav").exists()
        for i, (m, t) in enumerate(zip(meta, dialogue.turns, strict=True)))


def load_turn_audios(call_dir: Path, dialogue: Dialogue | None = None) -> list[TurnAudio] | None:
    """Audio des tours s'ils existent tous ET correspondent au dialogue actuel,
    sinon None (ligne de temps estimée). Un dialogue réécrit invalide l'ancien audio."""
    meta_path = call_dir / "turns.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text("utf-8"))
    if dialogue is not None and len(meta) != len(dialogue.turns):
        log.warning("%s : audio périmé (nombre de tours différent), ignoré", call_dir.name)
        return None
    audios = []
    for i, m in enumerate(meta):
        wav = call_dir / "turns" / f"{i:03d}.wav"
        if m is None or not wav.exists():
            return None
        if dialogue is not None and m.get("text_sent") != render_text(dialogue.turns[i]):
            log.warning("%s : audio périmé (tour %d modifié), ignoré", call_dir.name, i)
            return None
        with wave.open(str(wav)) as w:
            pcm, sr = w.readframes(w.getnframes()), w.getframerate()
        words = [tuple(x) for x in m["words"]] if m.get("words") else None
        audios.append(TurnAudio(pcm, sr, words))
    return audios


# --- Choix automatique des voix (bibliothèque partagée ElevenLabs) -------------

SPLIT_ORDER = ("train", "val", "test")


MIN_PER_SPLIT = 2  # vendeur et prospect du même genre : deux voix différentes par lot
MIN_PER_BUCKET = 3 * MIN_PER_SPLIT


def split_for(rank: int, n: int) -> str:
    """Répartit n voix d'un groupe (n >= 6) : 2 en val, 2 en test, le reste en train."""
    if n < MIN_PER_BUCKET:
        raise ValueError(f"{n} voix : il en faut au moins {MIN_PER_BUCKET} par langue et par genre")
    if rank < n - 2 * MIN_PER_SPLIT:
        return "train"
    return "val" if rank < n - MIN_PER_SPLIT else "test"


def choose_library_voices(candidates: list, language: str, per_bucket: int) -> list:
    """Filtre et classe les voix de la bibliothèque : langue native, sans tarif
    majoré ni préavis, usage conversationnel de préférence, puis les plus utilisées."""
    def native(v) -> bool:
        if (v.language or "") == language:
            return True
        return any(getattr(vl, "language", None) == language for vl in (v.verified_languages or []))

    ok = [v for v in candidates
          if native(v) and not v.rate and not v.fiat_rate and not v.notice_period
          and v.use_case not in ("characters_animation", "entertainment_tv")]
    ok.sort(key=lambda v: (v.use_case not in ("conversational", "social_media", "advertisement"),
                           -(v.cloned_by_count or 0)))
    # Diversité d'accents : un accent par voix tant que possible, puis les suivantes.
    chosen, seen = [], set()
    for v in ok:
        accent = getattr(v, "accent", None) or ""
        if accent not in seen:
            chosen.append(v)
            seen.add(accent)
    chosen += [v for v in ok if v not in chosen]
    return chosen[:per_bucket]


async def pick_voices(per_bucket: int = 6, dry_run: bool = False, path: Path | None = None) -> dict:
    """Sélectionne des voix natives FR/EN par genre, les ajoute au compte et écrit voices.json."""
    from elevenlabs import AsyncElevenLabs

    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY absent.")
    client = AsyncElevenLabs(api_key=key)
    # 1) Sélection de tous les groupes ; 2) vérification ; 3) seulement ensuite, ajout au compte.
    selection: dict[tuple[str, str], list] = {}
    for language in ("fr", "en"):
        for gender in ("female", "male"):
            res = await client.voices.get_shared(language=language, gender=gender, page_size=100,
                                                 include_custom_rates=False)
            selection[(language, gender)] = choose_library_voices(res.voices, language, per_bucket)
    short = {k: len(v) for k, v in selection.items() if len(v) < MIN_PER_BUCKET}
    if short:
        raise SystemExit(f"Pas assez de voix adaptées {short} (minimum {MIN_PER_BUCKET} par langue et genre). "
                         "Rien n'a été ajouté au compte. Complétez voices.json à la main (voices.example.json).")
    book: dict = {"fr": {}, "en": {}}
    for (language, gender), chosen in selection.items():
        entries = []
        for rank, v in enumerate(chosen):
            voice_id = v.voice_id
            if not dry_run and not v.is_added_by_user:
                added = await client.voices.share(v.public_owner_id, v.voice_id,
                                                  new_name=f"callhelp-{language}-{gender[0]}-{rank + 1}")
                voice_id = added.voice_id
            entries.append({"voice_id": voice_id, "name": v.name, "split": split_for(rank, len(chosen)),
                            "accent": v.accent, "preview_url": v.preview_url})
            log.info("%s/%s %-5s %s (%s)", language, gender, entries[-1]["split"], v.name, v.accent)
        book[language][gender] = entries
    if not dry_run:
        path = path or VOICES_FILE
        path.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("Voix enregistrées dans %s", path)
    return book
