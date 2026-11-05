"""Évaluation du moteur sur des appels annotés.

Rejoue chaque appel (transcription de référence) dans une vraie session, puis
compare les conseils émis à la vérité terrain :
- objections : précision / rappel / F1 par objection (fenêtre de tolérance) ;
- étapes : part des tours où l'étape suivie par le moteur est la bonne ;
- volume : conseils par minute.
L'émotion n'est pas évaluée ici : elle le sera sur l'audio, avec le modèle de voix.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path

from app.coach import NullProvider
from app.session import CallSession

from .assemble import TurnLabel
from .plan import OBJECTIONS

# Étapes du dialogue -> étapes du scénario suivies par le moteur.
STEP_MAP = {"announcement": "hook", "hook": "hook", "qualify": "qualify", "value": "value",
            "close": "close", "wrap_up": "close"}
TOLERANCE = 3.0  # secondes après la fin du tour pour émettre le conseil


async def run_call(replay: dict) -> tuple[list[dict], list[tuple[float, str]]]:
    """Rejoue un appel ; retourne (conseils, [(temps, étape suivie)])."""
    hints: list[dict] = []
    steps: list[tuple[float, str]] = []

    async def send(msg: dict) -> None:
        if msg["type"] == "hint":
            hints.append(msg["hint"])
        elif msg["type"] == "state":
            steps.append((msg["metrics"]["elapsed"], msg["step"]["id"]))

    session = CallSession(send, provider=NullProvider(), context_vars=replay.get("context", {}))
    await session.handle({"type": "start", "scenario_id": "cold_call_fr"})
    for event in replay["events"]:
        await session.handle(event)
    return hints, steps


def score_call(turns: list[TurnLabel], hints: list[dict], steps: list[tuple[float, str]]) -> dict:
    tp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)

    obj_hints = [h for h in hints if h["kind"] == "objection"]
    used: set[str] = set()
    for t in turns:
        if t.speaker != "prospect" or t.objection not in OBJECTIONS:
            continue
        match = next((h for h in obj_hints if h["id"] not in used and h["ref"] == t.objection
                      and t.start <= h["ts"] <= t.end + TOLERANCE), None)
        if match:
            tp[t.objection] += 1
            used.add(match["id"])
        else:
            fn[t.objection] += 1
    for h in obj_hints:
        if h["id"] not in used:
            fp[h["ref"]] += 1

    # Étape suivie à la fin de chaque tour vs étape de référence.
    correct = total = 0
    for t in turns:
        tracked = None
        for ts, step in steps:
            if ts <= t.end + 1e-6:
                tracked = step
        if tracked is not None:
            total += 1
            correct += tracked == STEP_MAP[t.step]

    duration = max((t.end for t in turns), default=0) / 60
    return {"tp": dict(tp), "fp": dict(fp), "fn": dict(fn), "step_correct": correct,
            "step_total": total, "hints": len(hints), "minutes": duration}


def aggregate(per_call: dict[str, dict]) -> dict:
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    step_c = step_t = hints = 0
    minutes = 0.0
    for r in per_call.values():
        for k, v in r["tp"].items():
            tp[k] += v
        for k, v in r["fp"].items():
            fp[k] += v
        for k, v in r["fn"].items():
            fn[k] += v
        step_c += r["step_correct"]
        step_t += r["step_total"]
        hints += r["hints"]
        minutes += r["minutes"]

    def prf(t: int, p: int, n: int) -> dict:
        precision = t / (t + p) if t + p else None
        recall = t / (t + n) if t + n else None
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
        return {"precision": precision, "recall": recall, "f1": f1, "support": t + n}

    objections = {o: prf(tp[o], fp[o], fn[o]) for o in OBJECTIONS}
    overall = prf(sum(tp.values()), sum(fp.values()), sum(fn.values()))
    return {
        "calls": len(per_call),
        "objections": objections,
        "objections_overall": overall,
        "step_accuracy": step_c / step_t if step_t else None,
        "hints_per_minute": hints / minutes if minutes else None,
    }


def evaluate_dir(calls_dir: Path) -> dict:
    """Évalue tous les appels d'un dossier (chaque sous-dossier : labels.json + replay.json)."""
    per_call: dict[str, dict] = {}
    for call in sorted(p for p in calls_dir.iterdir() if (p / "labels.json").exists()):
        turns = [TurnLabel.model_validate(t) for t in json.loads((call / "labels.json").read_text("utf-8"))]
        replay = json.loads((call / "replay.json").read_text("utf-8"))
        hints, steps = asyncio.run(run_call(replay))
        per_call[call.name] = score_call(turns, hints, steps)
    report = aggregate(per_call)
    by_lang: dict[str, dict[str, dict]] = defaultdict(dict)
    for name, r in per_call.items():
        by_lang[name.split("-")[0]][name] = r
    report["by_language"] = {lang: aggregate(calls) for lang, calls in sorted(by_lang.items())}
    return report


def format_report(report: dict) -> str:
    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x:.0%}"

    lines = [f"Appels évalués : {report['calls']}", "",
             "| Objection | Précision | Rappel | F1 | Occurrences |", "|---|---|---|---|---|"]
    for name, m in {**report["objections"], "TOTAL": report["objections_overall"]}.items():
        lines.append(f"| {name} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f1'])} | {m['support']} |")
    lines += ["", f"Étape correcte : {pct(report['step_accuracy'])} des tours",
              f"Conseils par minute : {report['hints_per_minute']:.1f}" if report["hints_per_minute"] else ""]
    for lang, r in report.get("by_language", {}).items():
        lines.append(f"- {lang} : objections F1 {pct(r['objections_overall']['f1'])}, "
                     f"rappel {pct(r['objections_overall']['recall'])}, étapes {pct(r['step_accuracy'])}")
    return "\n".join(lines)
