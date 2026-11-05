"""Rejoue un appel scripté dans une session et affiche les conseils.

    python -m demo.replay                    # démo cold call
    python -m demo.replay chemin/appel.json  # votre propre appel annoté
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.coach import default_provider
from app.session import CallSession

DEFAULT = Path(__file__).with_name("cold_call_demo.json")


async def replay(path: Path) -> list[dict]:
    script = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict] = []

    async def send(msg: dict) -> None:
        out.append(msg)
        if msg["type"] == "hint":
            h = msg["hint"]
            line = f"[{h['ts']:5.1f}s] {h['source']:4} {h['kind']:9} {h['text']}"
            if h.get("say"):
                line += f"\n{'':23}» {h['say']}"
            print(line)
        elif msg["type"] == "summary":
            print("\nBilan :", json.dumps(msg["summary"], ensure_ascii=False, indent=2))

    session = CallSession(send, provider=default_provider(), context_vars=script.get("context", {}))
    await session.handle({"type": "start", "scenario_id": script.get("scenario_id", "cold_call_fr")})
    for event in script["events"]:
        await session.handle(event)
        await asyncio.sleep(0)  # laisse tourner les tâches LLM en parallèle
    return out


if __name__ == "__main__":
    asyncio.run(replay(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT))
