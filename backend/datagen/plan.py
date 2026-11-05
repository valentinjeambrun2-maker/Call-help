"""Plan de couverture : 1 000 appels équilibrés et reproductibles.

Chaque appel reçoit une combinaison langue / secteur / persona / objections /
arc émotionnel / issue. Le tirage est déterministe (graine) et stratifié pour
que chaque valeur soit représentée de façon équilibrée.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Literal

from pydantic import BaseModel

EMOTIONS = ("interest", "hesitation", "annoyance", "boredom", "enthusiasm")
STATES = ("neutral",) + EMOTIONS

SECTORS = {
    "fr": [
        "cabinet d'expertise comptable", "agence immobilière", "restaurant", "artisan du bâtiment",
        "cabinet dentaire", "cabinet d'avocats", "salle de sport", "hôtel indépendant",
        "PME industrielle", "boutique e-commerce", "éditeur de logiciel B2B", "association",
    ],
    "en": [
        "accounting firm", "real estate agency", "restaurant", "building contractor",
        "dental practice", "law firm", "gym", "independent hotel",
        "manufacturing SME", "e-commerce store", "B2B software company", "non-profit",
    ],
}

ROLES = ["owner", "managing_director", "marketing_manager", "gatekeeper_assistant", "operations_manager"]
PERSONALITIES = ["rushed", "suspicious", "curious", "friendly", "blunt", "indecisive"]
SELLER_LEVELS = ["junior", "senior"]
GENDERS = ["female", "male"]
OBJECTIONS = ["no_time", "has_provider", "no_budget", "send_email"]

Outcome = Literal["meeting_booked", "callback", "send_info", "refused", "hung_up"]

# Arcs émotionnels du prospect, avec les issues cohérentes avec chacun.
ARCS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("neutral", "interest", "enthusiasm"), ("meeting_booked",)),
    (("hesitation", "interest", "enthusiasm"), ("meeting_booked",)),
    (("annoyance", "hesitation", "interest"), ("meeting_booked", "callback")),
    (("neutral", "hesitation", "interest"), ("meeting_booked", "callback", "send_info")),
    (("interest", "hesitation", "hesitation"), ("callback", "send_info")),
    (("neutral", "boredom", "boredom"), ("send_info", "refused")),
    (("annoyance", "annoyance", "annoyance"), ("refused", "hung_up")),
    (("interest", "boredom", "annoyance"), ("refused", "hung_up")),
    (("hesitation", "annoyance", "hesitation"), ("callback", "refused")),
    (("enthusiasm", "interest", "hesitation"), ("send_info", "callback")),
    (("boredom", "interest", "enthusiasm"), ("meeting_booked", "callback")),
    (("neutral", "enthusiasm", "enthusiasm"), ("meeting_booked",)),
    (("boredom", "boredom", "annoyance"), ("hung_up", "refused")),
]

Split = Literal["train", "val", "test"]


class CallSpec(BaseModel):
    call_id: str
    language: Literal["fr", "en"]
    sector: str
    prospect_role: str
    prospect_personality: str
    prospect_gender: str
    seller_level: str
    seller_gender: str
    objections: list[str]
    arc: list[str]
    outcome: Outcome
    target_minutes: float
    split: Split
    seed: int


def _cycle(rng: random.Random, values: list, n: int) -> list:
    """Répartition équilibrée : chaque valeur apparaît n/len(values) fois, ordre mélangé."""
    out = [values[i % len(values)] for i in range(n)]
    rng.shuffle(out)
    return out


def build_plan(n: int = 1000, seed: int = 7, fr_share: float = 0.6,
               split: tuple[float, float, float] = (0.8, 0.1, 0.1)) -> list[CallSpec]:
    rng = random.Random(seed)
    n_fr = round(n * fr_share)
    languages = ["fr"] * n_fr + ["en"] * (n - n_fr)
    rng.shuffle(languages)

    sector_idx = _cycle(rng, list(range(len(SECTORS["fr"]))), n)
    roles = _cycle(rng, ROLES, n)
    personalities = _cycle(rng, PERSONALITIES, n)
    p_genders = _cycle(rng, GENDERS, n)
    s_genders = _cycle(rng, GENDERS, n)
    levels = _cycle(rng, SELLER_LEVELS, n)
    arcs = _cycle(rng, list(range(len(ARCS))), n)
    # 0 à 3 objections : ~15 % sans objection, la majorité avec 1 ou 2.
    n_obj = _cycle(rng, [0] * 3 + [1] * 8 + [2] * 6 + [3] * 3, n)

    n_train = round(n * split[0])
    n_val = round(n * split[1])
    splits: list[Split] = ["train"] * n_train + ["val"] * n_val + ["test"] * (n - n_train - n_val)
    rng.shuffle(splits)

    specs = []
    obj_pool: list[str] = []
    for i in range(n):
        lang = languages[i]
        arc, outcomes = ARCS[arcs[i]]
        objections = []
        for _ in range(n_obj[i]):
            if not obj_pool:  # sac mélangé : chaque objection aussi souvent que les autres
                obj_pool = OBJECTIONS[:]
                rng.shuffle(obj_pool)
            o = obj_pool.pop()
            if o not in objections:
                objections.append(o)
        specs.append(CallSpec(
            call_id=f"{lang}-{i:04d}",
            language=lang,
            sector=SECTORS[lang][sector_idx[i]],
            prospect_role=roles[i],
            prospect_personality=personalities[i],
            prospect_gender=p_genders[i],
            seller_level=levels[i],
            seller_gender=s_genders[i],
            objections=objections,
            arc=list(arc),
            outcome=rng.choice(outcomes),
            target_minutes=round(rng.uniform(2.5, 4.5), 1),
            split=splits[i],
            seed=seed * 100_000 + i,
        ))
    return specs


def coverage(specs: list[CallSpec]) -> dict[str, dict[str, int]]:
    """Comptes par dimension, pour vérifier l'équilibre du plan."""
    dims = {
        "language": Counter(s.language for s in specs),
        "split": Counter(s.split for s in specs),
        "outcome": Counter(s.outcome for s in specs),
        "objection": Counter(o for s in specs for o in s.objections),
        "n_objections": Counter(str(len(s.objections)) for s in specs),
        "arc_state": Counter(e for s in specs for e in s.arc),
        "personality": Counter(s.prospect_personality for s in specs),
        "seller_level": Counter(s.seller_level for s in specs),
    }
    return {k: dict(sorted(v.items())) for k, v in dims.items()}
