"""API Call-help : santé, scénarios et WebSocket d'appel en direct."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from .coach import default_provider
from .scenario import list_scenarios
from .session import CallSession

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


@app.websocket("/ws/session")
async def ws_session(ws: WebSocket) -> None:
    await ws.accept()
    session = CallSession(ws.send_json, provider=default_provider())
    try:
        while True:
            try:
                # receive_text lève KeyError sur une trame binaire ; json.loads,
                # ValueError sur du JSON invalide : l'appel continue dans les deux cas.
                data = json.loads(await ws.receive_text())
                if not isinstance(data, dict):
                    raise ValueError("Message JSON attendu (objet)")
                if not await session.handle(data):
                    break
            except (ValidationError, ValueError, RuntimeError, FileNotFoundError, KeyError) as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
    await ws.close()
