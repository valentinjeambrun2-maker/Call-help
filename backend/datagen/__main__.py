"""Ligne de commande du générateur d'appels synthétiques.

    python -m datagen plan --n 1000
    python -m datagen dialogues --limit 50              # pilote, appels directs
    python -m datagen dialogues --batch submit          # 1 000 appels, Batches API
    python -m datagen dialogues --batch collect <id>
    python -m datagen estimate                          # caractères et coût voix
    python -m datagen voices --limit 50                 # ElevenLabs
    python -m datagen assemble                          # stéréo, étiquettes, rejeu
    python -m datagen evaluate                          # rapport du moteur

    python -m datagen voices-pick                       # choisit des voix natives FR/EN
    python -m datagen pilot --n 100                     # tout le pilote en une commande
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


DERIVED = ("turns.json", "provenance.json", "call.wav", "call_telephone.wav", "labels.json", "replay.json")


def save_dialogues(root: Path, ok: dict[str, Dialogue], errors: dict[str, str]) -> None:
    calls = root / "calls"
    for call_id, d in ok.items():
        call = calls / call_id
        call.mkdir(parents=True, exist_ok=True)
        path = call / "dialogue.json"
        new = d.model_dump_json(indent=1)
        if path.exists() and path.read_text("utf-8") != new:
            # Dialogue réécrit : l'audio et les étiquettes de l'ancien ne valent plus.
            for name in DERIVED:
                (call / name).unlink(missing_ok=True)
        path.write_text(new, encoding="utf-8")
    if errors:
        err_path = root / "dialogue_errors.json"
        previous = json.loads(err_path.read_text("utf-8")) if err_path.exists() else {}
        previous.update(errors)
        err_path.write_text(json.dumps(previous, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(ok)} dialogues valides, {len(errors)} échecs")


def record_usage(root: Path) -> None:
    from . import dialogue

    path = root / "usage.json"
    total = json.loads(path.read_text("utf-8")) if path.exists() else {"requests": 0, "input_tokens": 0,
                                                                         "output_tokens": 0, "cost_usd": 0.0}
    cost = dialogue.usage_cost() or 0.0
    for k in ("requests", "input_tokens", "output_tokens"):
        total[k] += dialogue.USAGE[k]
    total["cost_usd"] = round(total["cost_usd"] + cost, 4)
    path.write_text(json.dumps(total, indent=1), encoding="utf-8")
    print(f"Claude : {dialogue.USAGE['requests']} requêtes, {dialogue.USAGE['input_tokens']:,} jetons en entrée, "
          f"{dialogue.USAGE['output_tokens']:,} en sortie, ≈ {cost:.2f} $ (cumul {total['cost_usd']:.2f} $)")


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
        record_usage(args.root)


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
    # Filtrer d'abord, limiter ensuite : chaque relance avance sur les appels restants.
    todo = [(p, d) for p, d in iter_dialogues(args.root) if not voices.audio_complete(p, d)]
    if args.limit:
        todo = todo[: args.limit]
    asyncio.run(voices.synthesize_many(todo, specs, args.concurrency, args.model))


def cmd_voices_pick(args: argparse.Namespace) -> None:
    from . import voices

    asyncio.run(voices.pick_voices(args.per_bucket, dry_run=args.dry_run))


def cmd_pilot(args: argparse.Namespace) -> None:
    """Pilote : plan -> n dialogues -> contrôle du volume -> voix -> assemblage -> évaluation.
    Reprend là où il s'est arrêté : rien n'est refait (ni refacturé) si déjà présent."""
    from . import dialogue, voices
    from .evaluate import evaluate_dir, format_report

    if not (args.root / "plan.jsonl").exists():
        specs = build_plan(1000)
        args.root.mkdir(parents=True, exist_ok=True)
        (args.root / "plan.jsonl").write_text("\n".join(s.model_dump_json() for s in specs) + "\n", "utf-8")
    pilot = load_plan(args.root)[: args.n]
    calls = args.root / "calls"

    todo = select(pilot, None, only_missing=calls)
    if todo:
        print(f"Dialogues : {len(todo)} à écrire")
        ok, errors = asyncio.run(dialogue.write_many(todo, args.concurrency, args.company))
        save_dialogues(args.root, ok, errors)
        record_usage(args.root)

    ids = {s.call_id for s in pilot}
    specs_by_id = {s.call_id: s for s in pilot}
    written = [(p, d) for p, d in iter_dialogues(args.root) if d.call_id in ids]
    to_voice = [(p, d) for p, d in written if not voices.audio_complete(p, d)]
    chars = sum(voices.estimate_characters(d) for _, d in to_voice)
    print(f"Voix : {len(to_voice)} appels, {chars:,} caractères à synthétiser")
    if chars > args.max_chars:
        raise SystemExit(f"Arrêt : {chars:,} caractères dépassent --max-chars {args.max_chars:,}. "
                         "Relancez avec une limite plus haute si c'est voulu.")
    if args.skip_voices:
        print("Voix ignorées (--skip-voices) : assemblage avec durées estimées.")
    elif to_voice:
        specs = {s.call_id: s for s in pilot}
        asyncio.run(voices.synthesize_many(to_voice, specs, args.voice_concurrency, args.model))

    for path, d in written:
        write_call(path, d, voices.load_turn_audios(path, d),
                   context={"user_name": d.seller_name, "company": args.company},
                   language=specs_by_id[d.call_id].language)
    report = evaluate_dir(calls)
    (args.root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = format_report(report)
    (args.root / "report.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def cmd_assemble(args: argparse.Namespace) -> None:
    from .voices import load_turn_audios

    languages = {s.call_id: s.language for s in load_plan(args.root)}
    n = 0
    for path, d in iter_dialogues(args.root, args.limit):
        audios = load_turn_audios(path, d)
        write_call(path, d, audios, context={"user_name": d.seller_name, "company": args.company},
                   telephone=not args.no_telephone, language=languages.get(d.call_id, d.call_id[:2]))
        n += 1
    print(f"{n} appels assemblés")


def cmd_evaluate(args: argparse.Namespace) -> None:
    from .evaluate import evaluate_dir, format_report

    provider = None
    suffix = ""
    if args.llm:
        from app.coach.providers import PRICES, ClaudeProvider

        provider = ClaudeProvider()
        suffix = "_llm"
    report = evaluate_dir(args.root / "calls", provider)
    if provider is not None:
        pin, pout = PRICES.get(provider.model, (0, 0))
        u = provider.usage
        report["llm"] = {"model": provider.model, **u,
                         "cost_usd": round((u["input_tokens"] * pin + u["output_tokens"] * pout) / 1e6, 4)}
    (args.root / f"report{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = format_report(report)
    if provider is not None:
        text += f"\n\nLLM : {report['llm']['model']}, {u['requests']} requêtes, ≈ {report['llm']['cost_usd']:.2f} $"
    (args.root / f"report{suffix}.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    from app.env import load_env

    load_env()
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
    p.add_argument("--llm", action="store_true", help="avec le classement par Claude (sinon mots-clés seuls)")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("voices-pick", help="choisir des voix natives dans la bibliothèque ElevenLabs")
    p.add_argument("--per-bucket", type=int, default=6, help="voix par langue et par genre (6 minimum)")
    p.add_argument("--dry-run", action="store_true", help="lister sans ajouter au compte")
    p.set_defaults(func=cmd_voices_pick)

    p = sub.add_parser("pilot", help="pilote complet en une commande")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--concurrency", type=int, default=4, help="dialogues en parallèle")
    p.add_argument("--voice-concurrency", type=int, default=3, help="requêtes ElevenLabs simultanées")
    p.add_argument("--model", default=None, help="modèle de voix (défaut eleven_v4)")
    p.add_argument("--max-chars", type=int, default=600_000, help="garde-fou de dépense voix")
    p.add_argument("--skip-voices", action="store_true", help="dialogues + évaluation seulement")
    p.set_defaults(func=cmd_pilot)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
