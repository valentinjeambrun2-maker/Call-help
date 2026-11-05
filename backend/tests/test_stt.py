"""Transcription en direct contre un faux serveur ElevenLabs (protocole Scribe v2 Realtime)."""

import asyncio
import base64
import json

import pytest

from app.models import Speaker
from app.stt import FRAME_BYTES, ScribeChannel, sanitize_keyterms


class FakeScribe:
    """Après chaque bloc de `words_every` trames, valide un énoncé dont les mots couvrent
    l'audio reçu dans la session (temps relatifs à la session, comme l'API réelle)."""

    def __init__(self, words_every=10, drop_after=None, fatal=None):
        self.words_every, self.drop_after, self.fatal = words_every, drop_after, fatal
        self.sessions, self.urls, self.previous_texts = 0, [], []

    async def handler(self, ws):
        self.sessions += 1
        self.urls.append(ws.request.path)
        if self.fatal:
            await ws.send(json.dumps({"message_type": self.fatal, "error": "boom"}))
            await ws.close(1008)
            return
        await ws.send(json.dumps({"message_type": "session_started", "session_id": "s"}))
        received = 0
        async for raw in ws:
            m = json.loads(raw)
            if m.get("previous_text"):
                self.previous_texts.append(m["previous_text"])
            received += 1
            secs = received * 0.1
            if received % self.words_every == 0 or m.get("commit"):
                await ws.send(json.dumps({"message_type": "partial_transcript", "text": "bon"}))
                await ws.send(json.dumps({
                    "message_type": "committed_transcript_with_timestamps", "text": f" mot{received}",
                    "words": [{"type": "word", "text": "mot", "start": secs - 0.6, "end": secs - 0.1}]}))
            if self.drop_after and self.sessions == 1 and received >= self.drop_after:
                await ws.close(1000)  # fermeture côté serveur (comme l'inactivité)
                return


async def run_channel(fake, seconds, **kw):
    from websockets.asyncio.server import serve

    out, status = [], []

    async def on_t(msg):
        out.append(msg)

    async def on_s(level, message):
        status.append((level, message))

    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ch = ScribeChannel("k", Speaker.PROSPECT, "fr", on_t, on_s, url=f"ws://127.0.0.1:{port}/rt", **kw)
        ch.start()
        for _ in range(int(seconds * 10)):
            ch.push(bytes(FRAME_BYTES))
            await asyncio.sleep(0.01)
        await ch.close(timeout=2)
    return ch, out, status


async def test_finals_are_mapped_to_call_time_and_last_utterance_is_flushed():
    fake = FakeScribe(words_every=10)
    ch, out, _ = await run_channel(fake, 2.5)
    finals = [m for m in out if m.final]
    assert [round(m.end, 1) for m in finals[:2]] == [0.9, 1.9]
    assert finals[-1].end >= 2.4  # la fin d'appel valide et récupère le dernier énoncé
    assert all(m.speaker is Speaker.PROSPECT for m in out)
    assert any(not m.final for m in out)
    q = fake.urls[0]
    assert "language_code=fr" in q and "commit_strategy=vad" in q and "keepalive_interval_ms=1000" in q


async def test_reconnect_replays_untranscribed_audio_and_keeps_call_time():
    fake = FakeScribe(words_every=10, drop_after=15)
    ch, out, status = await run_channel(fake, 3.0)
    finals = [m for m in out if m.final]
    ends = [m.end for m in finals]
    assert fake.sessions >= 2 and any("reconnexion" in s for _, s in status)
    assert ends == sorted(ends) and len(set(ends)) == len(ends)  # ni doublon ni retour en arrière
    assert max(ends) > 2.0  # la suite de l'appel est transcrite avec les bons temps
    assert fake.previous_texts  # contexte transmis à la nouvelle session


async def test_fatal_error_is_reported_and_stops_the_channel():
    fake = FakeScribe(fatal="auth_error")
    ch, out, status = await run_channel(fake, 0.5)
    assert ch.fatal and status and status[-1][0] == "error" and "auth_error" in status[-1][1]
    assert fake.sessions == 1


def test_keyterms_respect_api_limits():
    terms = sanitize_keyterms(["Cabinet Duverger & Associés", "Claire", "", "Claire"] + [f"n{i}" for i in range(80)])
    assert "Cabinet" in terms and "Duverger" in terms and "Associés" in terms
    assert all(len(t) <= 20 for t in terms) and len(terms) <= 50 and terms.count("Claire") == 1
