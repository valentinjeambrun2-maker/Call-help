"""Chargement de backend/.env (clés d'API locales, jamais versionnées).

Format : une variable par ligne, CLE=valeur. Les variables déjà définies dans
l'environnement (terminal, environnement cloud) sont prioritaires.

Tolérant aux erreurs courantes sur Mac, qui sont signalées au démarrage :
fichier enregistré en texte enrichi par TextEdit (RTF), « export » en tête de ligne,
guillemets, espaces autour du « = ».
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE = Path(os.environ.get("CALLHELP_ENV_FILE") or Path(__file__).resolve().parent.parent / ".env")

# Constats sur le dernier chargement, pour le diagnostic du démarrage (jamais de valeur de clé).
NOTES: list[str] = []
FROM_FILE: set[str] = set()  # variables prises dans le fichier
OVERRIDDEN: set[str] = set()  # définies dans le fichier, mais l'environnement l'emporte
_signature: tuple | None = None  # (chemin, date de modification, taille) du dernier fichier lu
generation = 0  # incrémenté à chaque lecture : le diagnostic se refait quand il change
_path: Path = ENV_FILE  # fichier lu en dernier (celui que l'on surveille)


def file_signature(path: Path = ENV_FILE) -> tuple | None:
    for p in (path, path.with_name(path.name + ".txt"), path.with_name(path.name + ".rtf")):
        try:
            st = p.stat()
            return (str(p), st.st_mtime_ns, st.st_size)
        except OSError:
            continue
    return None


def reload_if_changed(path: Path | None = None) -> bool:
    """Relit le fichier s'il a changé depuis la dernière lecture (clé ajoutée ou corrigée sans
    relancer le backend). Retourne True s'il a été relu."""
    path = path or _path
    if file_signature(path) == _signature:
        return False
    load_env(path)
    return True


def load_env(path: Path = ENV_FILE) -> None:
    """Charge le fichier. Relu, il remplace les valeurs qu'il avait lui-même données ; une
    variable venue d'ailleurs (terminal, environnement cloud) reste prioritaire."""
    global _signature, generation, _path
    _path = path
    _signature = file_signature(path)
    generation += 1
    previous = set(FROM_FILE)
    NOTES.clear()
    FROM_FILE.clear()
    OVERRIDDEN.clear()
    seen: set[str] = set()
    try:
        _read(path, previous, seen)
    finally:
        for key in previous - seen:  # retirée du fichier depuis la dernière lecture
            os.environ.pop(key, None)


def _read(path: Path, previous: set[str], seen: set[str]) -> None:
    if not path.exists():
        # TextEdit et le Finder ajoutent volontiers une extension (« .env.txt », « .env.rtf »).
        alt = next((p for p in (path.with_name(path.name + ext) for ext in (".txt", ".rtf")) if p.exists()), None)
        if alt is None:
            NOTES.append(f"{path} introuvable : créez-le à partir de backend/.env.example")
            return
        NOTES.append(f"{alt.name} trouvé à la place de .env : lu quand même, mais renommez-le en « .env »")
        path = alt
    text = path.read_text("utf-8", errors="replace").lstrip("﻿")
    rtf = text.lstrip().startswith("{\\rtf")
    if rtf:
        NOTES.append(f"{path} est enregistré en texte enrichi (RTF, TextEdit) : lu quand même, mais "
                     "enregistrez-le en texte brut (TextEdit : Format > Convertir au format texte)")
    # Lu en entier d'abord : une clé écrite deux fois (ligne vide du modèle, puis la vraie clé
    # ajoutée en bas) prend la dernière valeur non vide, comme le veut l'usage des fichiers .env.
    values: dict[str, str] = {}
    counts: dict[str, int] = {}
    for line in text.splitlines():
        line = line.strip()
        if rtf:
            # « \f0\fs24 \cf0 CLE=valeur\ » : commandes RTF en tête, saut de ligne RTF en fin.
            line = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", line).strip().lstrip("{").rstrip("}").rstrip("\\").strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.sub(r"\s+#.*$", "", value)  # commentaire en fin de ligne : « CLE=valeur  # note »
        # Guillemets droits ou typographiques (TextEdit remplace " par “ ”).
        key, value = key.strip(), value.strip().strip("\"'“”‘’«» ").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        if value or key not in values:
            values[key] = value
    for key, n in counts.items():
        if n > 1:
            NOTES.append(f"{key} apparaît {n} fois dans {path.name} : la dernière valeur non vide est utilisée, "
                         "supprimez les autres lignes")
    for key, value in values.items():
        seen.add(key)
        if key in os.environ and key not in previous:  # venue du terminal ou de l'environnement cloud
            if os.environ[key] != value:
                OVERRIDDEN.add(key)
            continue
        os.environ[key] = value
        FROM_FILE.add(key)
