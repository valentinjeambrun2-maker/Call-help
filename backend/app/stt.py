"""Transcription en direct : ElevenLabs Scribe v2 Realtime, une session par canal.

Canal 0 = micro (l'utilisateur), canal 1 = onglet d'appel (le prospect). Le navigateur
envoie du PCM 16 bits mono à 16 kHz ; le backend relaie vers ElevenLabs (la clé ne
quitte jamais le serveur) et renvoie des TranscriptMsg partiels et définitifs.

Mesuré en direct (novembre 2026) : texte définitif ~0,65-1,05 s après la fin de la
parole avec une détection de silence (VAD) à 0,6 s. Prix : 0,39 $/h par canal, silence
compris. Choix et corrections issus de la vérification croisée :
- client WebSocket brut (le SDK 2.76 ne règle pas le keepalive et ignore les avertissements) ;
- langue toujours imposée (la détection auto a pris un « Euh... » français pour de l'espagnol) ;
- mots-clés <= 20 caractères (au-delà, la session est refusée), 50 au plus ;
- horloge de l'appel attachée à chaque bloc audio à sa réception : ni reconnexion ni
  retard d'envoi ne décalent les temps ;
- reconnexion avec rejeu de l'audio non encore transcrit, sur toute fermeture non voulue ;
- en fin d'appel, envoi du reste + validation, et attente du dernier texte (~1,5 s).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import urllib.parse
from collections import deque
from collections.abc import Awaitable, Callable

from .models import Speaker, TranscriptMsg

log = logging.getLogger(__name__)

SR = 16000
FRAME_SECONDS = 0.1
FRAME_BYTES = int(SR * 2 * FRAME_SECONDS)  # 100 ms de PCM 16 bits mono
URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
PRICE_PER_HOUR = 0.39

FATAL = {"auth_error", "quota_exceeded", "unaccepted_terms", "invalid_request", "input_error",
         "chunk_size_exceeded"}
RETRYABLE = {"error", "session_time_limit_exceeded", "transcriber_error", "resource_exhausted",
             "queue_overflow", "rate_limited", "insufficient_audio_activity", "commit_throttled"}

OnTranscript = Callable[[TranscriptMsg], Awaitable[None]]
OnStatus = Callable[[str, str], Awaitable[None]]  # (niveau, message)


class FatalSTT(Exception):
    pass


class RetrySTT(Exception):
    pass


def sanitize_keyterms(names: list[str], limit: int = 50) -> list[str]:
    """Noms propres utiles à la reconnaissance, chacun <= 20 caractères (limite de l'API)."""
    out: list[str] = []
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        parts = [name] if len(name) <= 20 else re.findall(r"[\w'’-]{3,20}", name)
        for p in parts:
            if p and p not in out and len(p) <= 20:
                out.append(p)
    return out[:limit]


class ScribeChannel:
    """Une session de transcription en direct pour un canal (un locuteur)."""

    def __init__(self, api_key: str, speaker: Speaker, language: str, on_transcript: OnTranscript,
                 on_status: OnStatus | None = None, vad_silence_secs: float = 0.6,
                 keyterms: list[str] | None = None, url: str = URL, max_queue_seconds: float = 60.0) -> None:
        self.api_key = api_key
        self.speaker = speaker
        self.language = language
        self.on_transcript = on_transcript
        self.on_status = on_status
        self.vad = vad_silence_secs
        self.keyterms = sanitize_keyterms(keyterms or [])
        self.url = url
        self.queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue(maxsize=int(max_queue_seconds / FRAME_SECONDS))
        self._pending = bytearray()
        self.received = 0.0  # horloge de l'appel : secondes d'audio reçues sur ce canal
        self.history: deque[tuple[float, bytes]] = deque(maxlen=int(15 / FRAME_SECONDS))  # 15 s rejouables
        self.committed_until = 0.0
        self.context = ""
        self.audio_sent_seconds = 0.0
        self._closing = asyncio.Event()
        self._flushed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.fatal: str | None = None

    # --- côté session ------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def push(self, pcm: bytes) -> None:
        """PCM 16 kHz mono, blocs de taille quelconque, dans l'ordre de capture."""
        if self._closing.is_set() or self.fatal:
            return
        self._pending += pcm
        while len(self._pending) >= FRAME_BYTES:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            item = (round(self.received, 3), frame)
            self.received += FRAME_SECONDS
            if self.queue.full():  # panne prolongée : on perd le plus ancien, les temps restent justes
                self.queue.get_nowait()
            self.queue.put_nowait(item)

    async def close(self, timeout: float = 2.0) -> None:
        """Fin d'appel : envoie le reste, valide, attend le dernier texte, puis ferme."""
        if self._task is not None and self._task.done():
            return
        if self._pending and not self.fatal:
            pad = bytes(self._pending) + bytes(FRAME_BYTES - len(self._pending))
            self._pending.clear()
            if not self.queue.full():
                self.queue.put_nowait((round(self.received, 3), pad))
                self.received += FRAME_SECONDS
        self._closing.set()
        if self._task:
            waiter = asyncio.create_task(self._flushed.wait())
            await asyncio.wait({waiter, self._task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            self._task.cancel()
            # Borne : sur un réseau bloqué, la poignée de main de fermeture peut durer 10 s.
            await asyncio.wait({self._task}, timeout=1.0)

    # --- connexion -----------------------------------------------------------------

    def _url(self) -> str:
        q = [("model_id", "scribe_v2_realtime"), ("audio_format", "pcm_16000"), ("commit_strategy", "vad"),
             ("vad_silence_threshold_secs", str(self.vad)), ("language_code", self.language),
             ("include_timestamps", "true"), ("keepalive_interval_ms", "1000")]
        q += [("keyterms", k) for k in self.keyterms]  # forme répétée (keyterms[] est ignoré)
        return self.url + "?" + urllib.parse.urlencode(q)

    async def _status(self, level: str, message: str) -> None:
        log.log(logging.ERROR if level == "error" else logging.INFO, "STT %s : %s", self.speaker.value, message)
        if self.on_status:
            try:
                await self.on_status(level, message)
            except Exception:  # noqa: BLE001 — un affichage raté ne doit pas couper la transcription
                log.exception("on_status")

    async def _run(self) -> None:
        from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

        backoff = 0.25
        failures = 0
        while True:
            # Connexion paresseuse : on attend le premier audio (ou la fin d'appel), sauf s'il reste
            # de l'audio envoyé mais pas encore transcrit (coupure juste avant de raccrocher).
            if self.queue.empty() and not self._replayable():
                getter = asyncio.create_task(self.queue.get())
                closer = asyncio.create_task(self._closing.wait())
                done, _ = await asyncio.wait({getter, closer}, return_when=asyncio.FIRST_COMPLETED)
                closer.cancel()
                if getter in done:
                    self._requeue_front(getter.result())
                else:
                    getter.cancel()
                    self._flushed.set()
                    return
            try:
                await self._session()
                backoff = 0.25
                failures = 0
                if self._closing.is_set():
                    self._flushed.set()
                    return
            except FatalSTT as exc:
                self.fatal = str(exc)
                await self._status("error", f"transcription arrêtée : {exc}")
                self._flushed.set()
                return
            except InvalidStatus as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                if code is not None and 400 <= code < 500 and code not in (408, 429):
                    # Erreur définitive (accès, paramètre, adresse) : inutile de réessayer.
                    self.fatal = f"refus d'ElevenLabs (HTTP {code})"
                    await self._status("error", f"transcription arrêtée : {self.fatal}")
                    self._flushed.set()
                    return
                failures += 1
                await self._status("info", f"reconnexion (HTTP {code})")
            except (RetrySTT, ConnectionClosed, InvalidHandshake, OSError, asyncio.TimeoutError) as exc:
                if self._closing.is_set():
                    self._flushed.set()
                    return
                failures += 1
                await self._status("info", f"reconnexion ({type(exc).__name__})")
            except Exception as exc:  # noqa: BLE001 — message inattendu : on journalise et on reconnecte
                log.exception("STT %s : erreur inattendue", self.speaker.value)
                if self._closing.is_set():
                    self._flushed.set()
                    return
                failures += 1
                await self._status("info", f"reconnexion après erreur ({type(exc).__name__})")
            if failures == 5:
                await self._status("error", "transcription en direct interrompue : reconnexions en échec")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 4.0)

    def _replayable(self) -> bool:
        return bool(self.history) and self.history[-1][0] >= self.committed_until - 0.05

    def _requeue_front(self, item: tuple[float, bytes]) -> None:
        rest = []
        while not self.queue.empty():
            rest.append(self.queue.get_nowait())
        for x in [item] + rest:
            if not self.queue.full():
                self.queue.put_nowait(x)

    async def _session(self) -> None:
        from websockets.asyncio.client import connect

        # Rejeu : l'audio envoyé mais pas encore transcrit avant la coupure.
        replay = [(t, b) for t, b in self.history if t >= self.committed_until - 0.05]
        async with connect(self._url(), additional_headers={"xi-api-key": self.api_key},
                           open_timeout=5, ping_interval=10, close_timeout=1, max_size=2**22) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if first.get("message_type") != "session_started":
                self._raise(first)
            origin: list[float | None] = [None]  # temps d'appel du premier bloc de cette session
            expected: list[float | None] = [None]
            last_final = asyncio.Event()
            commit_at: list[float | None] = [None]  # fin de l'audio couvert par la validation forcée

            async def send(t: float, frame: bytes, first_chunk: bool, commit: bool = False,
                           record: bool = True) -> None:
                if origin[0] is None:
                    origin[0] = t
                if expected[0] is not None and abs(t - expected[0]) > FRAME_SECONDS / 2:
                    # Trou dans l'audio (pertes) : nouvelle session pour garder des temps justes,
                    # qui repart du premier bloc après le trou (l'avant n'est plus rejoué).
                    self.history.clear()
                    raise RetrySTT("discontinuité audio")
                expected[0] = t + FRAME_SECONDS
                m = {"message_type": "input_audio_chunk", "audio_base_64": base64.b64encode(frame).decode(),
                     "commit": commit, "sample_rate": SR}
                if first_chunk and self.context:
                    m["previous_text"] = self.context[-50:]
                await ws.send(json.dumps(m))
                self.audio_sent_seconds += FRAME_SECONDS
                if record:  # un bloc rejoué est déjà dans l'historique
                    self.history.append((t, frame))

            async def sender() -> None:
                first_chunk = True
                for t, frame in replay:  # rattrapage ~4x temps réel
                    await send(t, frame, first_chunk, record=False)
                    first_chunk = False
                    await asyncio.sleep(0.025)
                while True:
                    if self._closing.is_set() and self.queue.empty():
                        # Fin d'appel : validation du dernier énoncé, puis attente de son texte.
                        if expected[0] is not None:
                            commit_at[0] = expected[0] - (origin[0] or 0.0)  # secondes de session couvertes
                            await send(expected[0], bytes(FRAME_BYTES), first_chunk, commit=True)
                            try:
                                await asyncio.wait_for(last_final.wait(), 1.5)
                            except asyncio.TimeoutError:
                                pass
                        await ws.close(1000)
                        return
                    try:
                        t, frame = await asyncio.wait_for(self.queue.get(), 0.25)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        await send(t, frame, first_chunk)
                    except BaseException:
                        # Trou, coupure pendant l'envoi ou annulation : ce bloc n'a pas été confirmé
                        # (absent de l'historique), il ouvrira la session suivante.
                        self._requeue_front((t, frame))
                        raise
                    first_chunk = False

            async def receiver() -> None:
                last_partial = None
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 20.0))
                    mt = m.get("message_type")
                    if mt == "partial_transcript":
                        text = (m.get("text") or "").lstrip(" .,")
                        if text and text != last_partial and origin[0] is not None:
                            last_partial = text
                            await self._safe(self.on_transcript, TranscriptMsg(
                                speaker=self.speaker, text=text, start=max(self.committed_until, origin[0]),
                                end=max(self.committed_until, origin[0] + self._session_seconds(expected, origin)),
                                final=False))
                    elif mt == "committed_transcript_with_timestamps":
                        last_partial = None
                        words = [w for w in (m.get("words") or [])
                                 if w.get("type") == "word" and (w.get("text") or "").strip(" .,?!…")
                                 and isinstance(w.get("start"), (int, float)) and isinstance(w.get("end"), (int, float))]
                        text = (m.get("text") or "").strip().lstrip(".,").strip()
                        if text and words and origin[0] is not None:
                            start = round(origin[0] + words[0]["start"], 3)
                            end = round(origin[0] + words[-1]["end"], 3)
                            if end > self.committed_until:  # sinon doublon après rejeu
                                self.committed_until = end
                                self.context = (self.context + " " + text)[-200:]
                                try:
                                    await self.on_transcript(TranscriptMsg(
                                        speaker=self.speaker, text=text, start=max(0.0, start), end=end, final=True))
                                except Exception:  # noqa: BLE001 — une erreur en aval ne coupe pas la transcription
                                    log.exception("STT %s : traitement d'un énoncé", self.speaker.value)
                        # Fin d'appel : on ne libère l'attente que sur le texte de la validation forcée
                        # (ou une validation vide), pas sur un énoncé précédent encore en route.
                        if commit_at[0] is not None:
                            last_end = words[-1]["end"] if words else None
                            # Le dernier énoncé finit au plus tard « vad » avant la validation (sinon la
                            # détection de silence l'aurait déjà validé) ; marge pour l'imprécision des temps.
                            if last_end is None or last_end >= commit_at[0] - (self.vad + 0.3):
                                last_final.set()
                    elif mt in FATAL or mt in RETRYABLE:
                        self._raise(m)

            tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            if not self._closing.is_set():
                raise RetrySTT("fermeture inattendue")  # toute fermeture non voulue : reconnexion

    async def _safe(self, fn, *args) -> None:
        try:
            await fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("STT %s : rappel", self.speaker.value)

    @staticmethod
    def _session_seconds(expected: list[float | None], origin: list[float | None]) -> float:
        if expected[0] is None or origin[0] is None:
            return 0.0
        return expected[0] - origin[0]

    @staticmethod
    def _raise(m: dict) -> None:
        mt = m.get("message_type")
        raise (FatalSTT if mt in FATAL else RetrySTT)(f"{mt}: {m.get('error') or m.get('message') or ''}".strip())


class SttHub:
    """Les deux canaux d'un appel."""

    def __init__(self, api_key: str, language: str, on_transcript: OnTranscript, on_status: OnStatus,
                 keyterms: list[str] | None = None, url: str = URL) -> None:
        self.channels = {
            0: ScribeChannel(api_key, Speaker.USER, language, on_transcript, on_status, url=url),
            1: ScribeChannel(api_key, Speaker.PROSPECT, language, on_transcript, on_status,
                             keyterms=keyterms, url=url),
        }

    def start(self) -> None:
        for ch in self.channels.values():
            ch.start()

    def push(self, data: bytes) -> None:
        """Trame binaire du navigateur : 1 octet de canal (0 ou 1) + PCM 16 bits 16 kHz."""
        if not data:
            return
        ch = self.channels.get(data[0])
        if ch is not None:
            ch.push(data[1:])

    async def close(self) -> None:
        await asyncio.gather(*(ch.close() for ch in self.channels.values()))

    @property
    def cost_usd(self) -> float:
        return sum(ch.audio_sent_seconds for ch in self.channels.values()) / 3600 * PRICE_PER_HOUR
