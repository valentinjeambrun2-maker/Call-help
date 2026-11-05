"""Bibliothèque des appels de démonstration (générés par datagen).

Deux sources, la première prioritaire :
- `backend/demo/calls/<id>/` : sélection versionnée dans le dépôt (MP3 + étiquettes),
  disponible dès un `git pull` ;
- `backend/data/synthetic/calls/<id>/` : appels générés localement (WAV), non versionnés.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
SOURCES = {
    "repo": BACKEND / "demo" / "calls",
    "local": BACKEND / "data" / "synthetic" / "calls",
}
CALL_ID = re.compile(r"^(fr|en)-\d{4}$")
AUDIO_NAMES = ("call.mp3", "call.wav")


def _call_dir(call_id: str) -> tuple[Path, str] | None:
    if not CALL_ID.match(call_id):
        return None
    for source, root in SOURCES.items():
        d = root / call_id
        if (d / "labels.json").exists() and (d / "replay.json").exists():
            return d, source
    return None


def audio_path(call_id: str) -> Path | None:
    found = _call_dir(call_id)
    if not found:
        return None
    for name in AUDIO_NAMES:
        if (found[0] / name).exists():
            return found[0] / name
    return None


def _meta(d: Path) -> dict:
    """Métadonnées d'un appel : meta.json (sélection exportée) ou dialogue.json (génération locale)."""
    if (d / "meta.json").exists():
        return json.loads((d / "meta.json").read_text("utf-8"))
    labels = json.loads((d / "labels.json").read_text("utf-8"))
    dialogue = json.loads((d / "dialogue.json").read_text("utf-8")) if (d / "dialogue.json").exists() else {}
    return describe(d.name, labels, dialogue)


def describe(call_id: str, labels: list[dict], dialogue: dict) -> dict:
    prospect = [t for t in labels if t["speaker"] == "prospect"]
    objections = []
    for t in prospect:
        if t.get("objection") and t["objection"] != "other" and t["objection"] not in objections:
            objections.append(t["objection"])
    emotions = []
    for t in prospect:
        if t["emotion"] != "neutral" and t["emotion"] not in emotions:
            emotions.append(t["emotion"])
    return {
        "id": call_id,
        "language": call_id[:2],
        "duration": round(max((t["end"] for t in labels), default=0), 1),
        "turns": len(labels),
        "objections": objections,
        "emotions": emotions,
        "outcome": dialogue.get("outcome"),
        "offer": dialogue.get("offer"),
        "prospect_company": dialogue.get("prospect_company"),
        "seller_name": dialogue.get("seller_name"),
        "prospect_name": dialogue.get("prospect_name"),
    }


def list_calls() -> list[dict]:
    seen: dict[str, dict] = {}
    for source, root in SOURCES.items():
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if d.name in seen or not CALL_ID.match(d.name) or not (d / "labels.json").exists():
                continue
            meta = _meta(d)
            meta["source"] = source
            meta["has_audio"] = any((d / n).exists() for n in AUDIO_NAMES)
            seen[d.name] = meta
    # Sélection du dépôt d'abord, puis par langue et identifiant.
    return sorted(seen.values(), key=lambda m: (m["source"] != "repo", m["language"], m["id"]))


def load_json(call_id: str, name: str) -> dict | list | None:
    found = _call_dir(call_id)
    if not found or name not in ("labels.json", "replay.json"):
        return None
    return json.loads((found[0] / name).read_text("utf-8"))
