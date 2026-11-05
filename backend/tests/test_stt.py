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


# --- Régressions de la deuxième vérification indépendante ------------------------------

class DropFake(FakeScribe):
    """Coupe les sessions listées après `drop_after` trames reçues ; aucune validation hors fin d'appel."""

    def __init__(self, drop_sessions, drop_after, old_final_on_commit=False):
        super().__init__(words_every=10**6)
        self.drop_sessions, self.drop_after = set(drop_sessions), drop_after
        self.old_final_on_commit = old_final_on_commit
        self.first_t: list[int] = []

    async def handler(self, ws):
        self.sessions += 1
        session = self.sessions
        await ws.send(json.dumps({"message_type": "session_started", "session_id": "s"}))
        received = 0
        async for raw in ws:
            m = json.loads(raw)
            received += 1
            secs = received * 0.1
            if m.get("commit"):
                if self.old_final_on_commit:
                    # Validation par silence d'un énoncé précédent, encore en route à la fin d'appel.
                    await ws.send(json.dumps({
                        "message_type": "committed_transcript_with_timestamps", "text": " ancien",
                        "words": [{"type": "word", "text": "ancien", "start": 0.1, "end": 0.4}]}))
                    await asyncio.sleep(0.4)
                await ws.send(json.dumps({
                    "message_type": "committed_transcript_with_timestamps", "text": " dernier",
                    "words": [{"type": "word", "text": "dernier", "start": secs - 0.6, "end": secs - 0.1}]}))
            if session in self.drop_sessions and received >= self.drop_after:
                await ws.close(1000)
                return


async def _channel(fake_handler, **kw):
    from websockets.asyncio.server import serve

    out, status = [], []

    async def on_t(msg):
        out.append(msg)

    async def on_s(level, message):
        status.append((level, message))

    server = await serve(fake_handler, "127.0.0.1", 0, **kw.pop("serve_kw", {}))
    port = server.sockets[0].getsockname()[1]
    ch = ScribeChannel("k", Speaker.PROSPECT, "fr", on_t, on_s, url=f"ws://127.0.0.1:{port}/rt", **kw)
    ch.start()
    return server, ch, out, status


async def test_second_reconnect_does_not_loop_on_replayed_history():
    fake = DropFake(drop_sessions={1, 2}, drop_after=12)
    server, ch, out, _ = await _channel(fake.handler)
    for _ in range(30):
        ch.push(bytes(FRAME_BYTES))
        await asyncio.sleep(0.05)
    await ch.close(timeout=2)
    server.close()
    assert fake.sessions == 3  # avant : le rejeu réenregistré créait un trou, et l'on bouclait
    assert any(m.final and m.end > 2.5 for m in out)


async def test_audio_gap_after_overflow_starts_a_clean_session():
    fake = DropFake(drop_sessions={1}, drop_after=5)
    server, ch, out, _ = await _channel(fake.handler, max_queue_seconds=0.5)
    for _ in range(30):  # 3 s d'audio d'un coup : la file (0,5 s) déborde pendant la coupure
        ch.push(bytes(FRAME_BYTES))
        await asyncio.sleep(0.005)
    await asyncio.sleep(1.5)
    await ch.close(timeout=2)
    server.close()
    assert fake.sessions <= 3  # avant : chaque session rejouait l'avant-trou et retombait dessus
    assert any(m.final and m.end >= 2.9 for m in out)


async def test_in_flight_silence_final_does_not_drop_the_last_utterance():
    fake = DropFake(drop_sessions=set(), drop_after=0, old_final_on_commit=True)
    server, ch, out, _ = await _channel(fake.handler)
    for _ in range(30):
        ch.push(bytes(FRAME_BYTES))
        await asyncio.sleep(0.01)
    await ch.close(timeout=3)
    server.close()
    texts = [m.text for m in out if m.final]
    assert "ancien" in texts and "dernier" in texts


async def test_close_is_bounded_when_the_server_goes_silent():
    """Serveur WebSocket minimal qui accepte la session puis ne lit plus rien."""
    import hashlib
    import time

    async def silent(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        key = next(line.split(b":", 1)[1].strip() for line in head.split(b"\r\n")
                   if line.lower().startswith(b"sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        payload = json.dumps({"message_type": "session_started"}).encode()
        writer.write(bytes([0x81, len(payload)]) + payload)
        await writer.drain()
        await asyncio.sleep(30)

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    ch = ScribeChannel("k", Speaker.USER, "fr", lambda m: asyncio.sleep(0), url=f"ws://127.0.0.1:{port}/rt")
    ch.start()
    for _ in range(5):
        ch.push(bytes(FRAME_BYTES))
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)
    t0 = time.monotonic()
    await ch.close(timeout=2)
    assert time.monotonic() - t0 < 4  # avant : ~12 s (poignée de main de fermeture de 10 s)
    server.close()


async def test_permanent_http_rejection_stops_instead_of_retrying_forever():
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    calls = []

    async def handler(ws):  # jamais atteint
        pass

    def reject(conn, request):
        calls.append(1)
        return Response(400, "Bad Request", Headers(), b"bad keyterms")

    server, ch, out, status = await _channel(handler, serve_kw={"process_request": reject})
    ch.push(bytes(FRAME_BYTES))
    for _ in range(30):
        if ch.fatal:
            break
        await asyncio.sleep(0.05)
    await ch.close(timeout=1)
    server.close()
    assert ch.fatal and "400" in ch.fatal and len(calls) == 1
    assert status and status[-1][0] == "error"


async def test_callback_error_and_malformed_words_do_not_kill_the_channel():
    from websockets.asyncio.server import serve

    async def handler(ws):
        await ws.send(json.dumps({"message_type": "session_started"}))
        n = 0
        async for raw in ws:
            n += 1
            if n == 3:  # mot sans temps : ignoré
                await ws.send(json.dumps({"message_type": "committed_transcript_with_timestamps", "text": "x",
                                          "words": [{"type": "word", "text": "x"}]}))
            if n in (5, 10):
                await ws.send(json.dumps({"message_type": "committed_transcript_with_timestamps",
                                          "text": f"mot{n}", "words": [{"type": "word", "text": "mot",
                                                                        "start": n * 0.1 - 0.3, "end": n * 0.1}]}))

    got = []

    async def on_t(msg):
        if msg.final and not got:
            got.append("boom")
            raise RuntimeError("erreur en aval")
        if msg.final:
            got.append(msg.text)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ch = ScribeChannel("k", Speaker.USER, "fr", on_t, url=f"ws://127.0.0.1:{port}/rt")
        ch.start()
        for _ in range(12):
            ch.push(bytes(FRAME_BYTES))
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        await ch.close(timeout=1)
    assert got[:2] == ["boom", "mot10"]


async def test_frame_in_flight_during_a_network_cut_is_not_lost(monkeypatch):
    from websockets.asyncio.connection import Connection
    from websockets.asyncio.server import serve
    from websockets.exceptions import ConnectionClosedError

    seen, sessions, calls = set(), [0], [0]
    real_send = Connection.send

    async def flaky_send(self, message, *a, **k):
        calls[0] += 1
        if calls[0] == 10:  # coupure détectée pendant l'envoi de ce bloc
            self.transport.abort()
            raise ConnectionClosedError(None, None)
        return await real_send(self, message, *a, **k)

    async def handler(ws):
        sessions[0] += 1
        await ws.send(json.dumps({"message_type": "session_started"}))
        async for raw in ws:
            seen.add(int.from_bytes(base64.b64decode(json.loads(raw)["audio_base_64"])[:4], "little"))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(Connection, "send", flaky_send)
        ch = ScribeChannel("k", Speaker.USER, "fr", lambda m: asyncio.sleep(0), url=f"ws://127.0.0.1:{port}/rt")
        ch.start()
        for i in range(1, 31):
            ch.push(i.to_bytes(4, "little") + bytes(FRAME_BYTES - 4))
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.5)
        await ch.close(timeout=2)
    assert sessions[0] == 2 and set(range(1, 31)) <= seen  # aucun bloc perdu à la coupure


async def test_cut_just_before_hang_up_still_transcribes_the_pending_audio():
    from websockets.asyncio.server import serve

    sessions = [0]

    async def handler(ws):
        sessions[0] += 1
        await ws.send(json.dumps({"message_type": "session_started"}))
        n = 0
        async for raw in ws:
            n += 1
            m = json.loads(raw)
            if sessions[0] == 1 and n == 20:
                await ws.close(1000)  # coupure, puis plus aucun audio : l'appel se termine
                return
            if m.get("commit"):
                await ws.send(json.dumps({"message_type": "committed_transcript_with_timestamps", "text": "fin",
                                          "words": [{"type": "word", "text": "fin", "start": 0.2, "end": n * 0.1 - 0.1}]}))

    out = []

    async def on_t(msg):
        out.append(msg)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ch = ScribeChannel("k", Speaker.USER, "fr", on_t, url=f"ws://127.0.0.1:{port}/rt")
        ch.start()
        for _ in range(20):
            ch.push(bytes(FRAME_BYTES))
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.6)  # coupure détectée, aucun nouvel audio
        await ch.close(timeout=3)
    assert sessions[0] == 2 and any(m.final and m.text == "fin" for m in out)
