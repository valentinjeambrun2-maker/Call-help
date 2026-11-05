"""API Call-help : santé, scénarios et WebSocket d'appel en direct."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .env import load_env

load_env()  # avant toute lecture de clé d'API

from .coach import default_provider  # noqa: E402
from .coach.summary import default_summary_provider  # noqa: E402
from . import demo_library  # noqa: E402
from .scenario import list_scenarios  # noqa: E402
from .session import CallSession  # noqa: E402

log = logging.getLogger("callhelp")

app = FastAPI(title="Call-help", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=os.getenv("CALLHELP_CORS", r"^(chrome-extension://.*|http://localhost(:\d+)?)$"),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/scenarios")
def scenarios() -> list[dict]:
    return list_scenarios()


DEMO_FILE = Path(__file__).resolve().parent.parent / "demo" / "cold_call_demo.json"


@app.get("/demo")
def demo() -> dict:
    """Appel scripté rejoué par le panneau (mode démo)."""
    return json.loads(DEMO_FILE.read_text(encoding="utf-8"))


@app.get("/demo/calls")
def demo_calls() -> list[dict]:
    """Appels générés disponibles pour les démos (sélection du dépôt + génération locale)."""
    return demo_library.list_calls()


@app.get("/demo/calls/{call_id}/audio")
def demo_call_audio(call_id: str) -> FileResponse:
    path = demo_library.audio_path(call_id)
    if path is None:
        raise HTTPException(404, "Appel ou audio introuvable")
    return FileResponse(path, media_type="audio/mpeg" if path.suffix == ".mp3" else "audio/wav")


@app.get("/demo/calls/{call_id}/{kind}")
def demo_call_data(call_id: str, kind: str):
    data = demo_library.load_json(call_id, f"{kind}.json") if kind in ("labels", "replay") else None
    if data is None:
        raise HTTPException(404, "Appel introuvable")
    return data


async def _await_ai_summary(ws: WebSocket, session: CallSession) -> None:
    """Attend le bilan IA tout en écoutant le client : s'il se déconnecte, on l'annule."""
    import asyncio

    task = session.summary_task
    while task is not None and not task.done():
        receiver = asyncio.create_task(ws.receive())
        done, _ = await asyncio.wait({task, receiver}, return_when=asyncio.FIRST_COMPLETED)
        if receiver in done:
            frame = receiver.result()
            if frame.get("type") == "websocket.disconnect":
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return
            continue  # message tardif du client : ignoré, on attend toujours le bilan
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)


@app.websocket("/ws/session")
async def ws_session(ws: WebSocket) -> None:
    await ws.accept()

    async def safe_send(msg: dict) -> None:
        # La transcription en direct peut produire un texte après la fermeture du client.
        try:
            await ws.send_json(msg)
        except (RuntimeError, WebSocketDisconnect):
            pass

    session = CallSession(safe_send, provider=default_provider(), summary_provider=default_summary_provider())
    audio_refused = False
    try:
        while True:
            try:
                frame = await ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:
                    # Audio (1 octet de canal + PCM 16 kHz) pour la transcription en direct.
                    if not session.push_audio(frame["bytes"]) and not audio_refused:
                        audio_refused = True  # signalé une fois, pas à chaque bloc de 100 ms
                        await safe_send({"type": "error", "message": "Audio reçu sans transcription en direct : "
                                         "démarrez l'appel avec transcription='server'."})
                    continue
                data = json.loads(frame.get("text") or "")
                if not isinstance(data, dict):
                    raise ValueError("Message JSON attendu (objet)")
                if not await session.handle(data):
                    await _await_ai_summary(ws, session)
                    break
            except (ValidationError, ValueError, RuntimeError, FileNotFoundError, KeyError) as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
    try:
        await ws.close()
    except RuntimeError:
        pass  # déjà fermée par le client
