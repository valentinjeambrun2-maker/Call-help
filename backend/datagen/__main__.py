"""Ligne de commande du générateur d'appels synthétiques.

    python -m datagen plan --n 1000
    python -m datagen dialogues --limit 50              # pilote, appels directs
    python -m datagen dialogues --batch submit          # 1 000 appels, Batches API
    python -m datagen dialogues --batch collect <id>
    python -m datagen estimate                          # caractères et coût voix
    python -m datagen voices --limit 50                 # ElevenLabs
    python -m datagen assemble                          # stéréo, étiquettes, rejeu
    python -m datagen evaluate                          # rapport du moteur
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .assemble import write_call
from .dialogue import WORDS_PER_MINUTE, Dialogue
from .plan import CallSpec, build_plan, coverage

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "data" / "synthetic"


def load_plan(root: Path) -> list[CallSpec]:
    path = root / "plan.jsonl"
    if not path.exists():
        raise SystemExit(f"Plan absent : lancez d'abord `python -m datagen plan` ({path})")
    return [CallSpec.model_validate_json(line) for line in path.read_text("utf-8").splitlines() if line]


def select(specs: list[CallSpec], limit: int | None, only_missing: Path | None = None) -> list[CallSpec]:
    if only_missing:
        specs = [s for s in specs if not (only_missing / s.call_id / "dialogue.json").exists()]
    return specs[:limit] if limit else specs


def save_dialogues(root: Path, ok: dict[str, Dialogue], errors: dict[str, str]) -> None:
    calls = root / "calls"
    for call_id, d in ok.items():
        (calls / call_id).mkdir(parents=True, exist_ok=True)
        (calls / call_id / "dialogue.json").write_text(d.model_dump_json(indent=1), encoding="utf-8")
    if errors:
        err_path = root / "dialogue_errors.json"
        previous = json.loads(err_path.read_text("utf-8")) if err_path.exists() else {}
        previous.update(errors)
        err_path.write_text(json.dumps(previous, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(ok)} dialogues valides, {len(errors)} échecs")


def cmd_plan(args: argparse.Namespace) -> None:
    specs = build_plan(args.n, seed=args.seed)
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "plan.jsonl").write_text("\n".join(s.model_dump_json() for s in specs) + "\n", encoding="utf-8")
    print(json.dumps(coverage(specs), ensure_ascii=False, indent=1))
    print(f"{len(specs)} appels planifiés -> {args.root / 'plan.jsonl'}")


def cmd_dialogues(args: argparse.Namespace) -> None:
    from . import dialogue

    specs = load_plan(args.root)
    calls = args.root / "calls"
    if args.batch == "submit":
        todo = select(specs, args.limit, only_missing=calls)
        batch_id = dialogue.submit_batch(todo, args.company)
        (args.root / "batches.txt").open("a").write(batch_id + "\n")
        print(f"Batch envoyé : {batch_id} ({len(todo)} appels). Récupération : "
              f"python -m datagen dialogues --batch collect {batch_id}")
    elif args.batch == "collect":
        ok, errors = dialogue.collect_batch(args.batch_id, specs)
        save_dialogues(args.root, ok, errors)
    else:
        todo = select(specs, args.limit, only_missing=calls)
        ok, errors = asyncio.run(dialogue.write_many(todo, args.concurrency, args.company))
        save_dialogues(args.root, ok, errors)


def iter_dialogues(root: Path, limit: int | None = None):
    paths = sorted((root / "calls").glob("*/dialogue.json"))
    for path in paths[:limit] if limit else paths:
        yield path.parent, Dialogue.model_validate_json(path.read_text("utf-8"))


def cmd_estimate(args: argparse.Namespace) -> None:
    from .voices import estimate_characters

    written = list(iter_dialogues(args.root))
    if written:
        chars = sum(estimate_characters(d) for _, d in written)
        n = len(written)
        print(f"{n} dialogues écrits : {chars:,} caractères à synthétiser (balises comprises)")
        per_call = chars / n
    else:
        specs = load_plan(args.root)
        words = sum(s.target_minutes * WORDS_PER_MINUTE for s in specs)
        per_call = words * 6 * 1.1 / len(specs)
        print("Aucun dialogue écrit : estimation depuis le plan (6 caractères/mot, +10 % de balises)")
    total = per_call * args.calls
    print(f"≈ {per_call:,.0f} caractères par appel ; {args.calls} appels ≈ {total:,.0f} caractères")
    print(f"Crédits ElevenLabs à {args.credits_per_char} crédit/caractère : {total * args.credits_per_char:,.0f}")


def cmd_voices(args: argparse.Namespace) -> None:
    from . import voices

    specs = {s.call_id: s for s in load_plan(args.root)}
    todo = [(p, d) for p, d in iter_dialogues(args.root, args.limit) if not (p / "turns.json").exists()]
    asyncio.run(voices.synthesize_many(todo, specs, args.concurrency, args.model))


def cmd_assemble(args: argparse.Namespace) -> None:
    from .voices import load_turn_audios

    n = 0
    for path, d in iter_dialogues(args.root, args.limit):
        audios = load_turn_audios(path)
        write_call(path, d, audios, context={"user_name": d.seller_name, "company": args.company},
                   telephone=not args.no_telephone)
        n += 1
    print(f"{n} appels assemblés")


def cmd_evaluate(args: argparse.Namespace) -> None:
    from .evaluate import evaluate_dir, format_report

    report = evaluate_dir(args.root / "calls")
    (args.root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = format_report(report)
    (args.root / "report.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="python -m datagen", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="dossier de travail")
    parser.add_argument("--company", default="The Herakles", help="entreprise du vendeur")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="plan de couverture")
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("dialogues", help="écriture des dialogues par Claude")
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--batch", choices=["submit", "collect"])
    p.add_argument("batch_id", nargs="?")
    p.set_defaults(func=cmd_dialogues)

    p = sub.add_parser("estimate", help="volume de caractères et crédits voix")
    p.add_argument("--calls", type=int, default=1000)
    p.add_argument("--credits-per-char", type=float, default=1.0)
    p.set_defaults(func=cmd_estimate)

    p = sub.add_parser("voices", help="synthèse vocale ElevenLabs")
    p.add_argument("--limit", type=int)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--model", default=None)
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("assemble", help="stéréo, étiquettes et rejeu")
    p.add_argument("--limit", type=int)
    p.add_argument("--no-telephone", action="store_true")
    p.set_defaults(func=cmd_assemble)

    p = sub.add_parser("evaluate", help="évaluer le moteur sur les appels")
    p.set_defaults(func=cmd_evaluate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
