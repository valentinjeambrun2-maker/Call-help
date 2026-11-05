"""Diagnostic des clés au démarrage : la transcription et les conseils IA sont-ils prêts ?

Vérifie chaque clé par une requête gratuite, sans rien transcrire ni générer :
- ElevenLabs : demande d'un jeton à usage unique pour la transcription en direct. Une clé
  invalide y reçoit la vraie raison (« la clé doit faire 51 caractères, elle en fait 52 »),
  alors que la session de transcription ne répond que « You must be authenticated » ;
- Anthropic : liste des modèles.

Le résultat est écrit dans le terminal du backend et servi par /health, que la page de
démo et le panneau affichent. Aucune valeur de clé n'est jamais écrite ni renvoyée.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from . import env

log = logging.getLogger("uvicorn.error")  # affiché dans le terminal d'uvicorn

ELEVENLABS_TOKEN_URL = "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe"
ELEVENLABS_KEY_LENGTH = 51


@dataclass
class Check:
    ready: bool
    message: str  # en français, pour l'utilisateur
    source: str | None = None  # « backend/.env » ou « environnement »


def _source(name: str) -> str | None:
    if name not in os.environ:
        return None
    return "backend/.env" if name in env.FROM_FILE else "environnement du terminal"


def _format_problem(name: str, value: str) -> str | None:
    """Défaut visible sans appeler le service (sans jamais montrer la clé)."""
    if any(c.isspace() for c in value):
        return "elle contient un espace ou un retour à la ligne"
    if any(c in value for c in "\"'\\{}"):
        return "elle contient un guillemet, une barre oblique inverse ou une accolade (copie depuis un document ?)"
    if name == "ELEVENLABS_API_KEY":
        if not value.startswith("sk_"):
            return "une clé ElevenLabs commence par « sk_ »"
        if len(value) != ELEVENLABS_KEY_LENGTH:
            return f"elle fait {len(value)} caractères au lieu de {ELEVENLABS_KEY_LENGTH}"
    if name == "ANTHROPIC_API_KEY" and not value.startswith("sk-ant-"):
        return "une clé Anthropic commence par « sk-ant- »"
    return None


def _where(name: str) -> str:
    src = _source(name)
    out = f" (clé lue depuis {src})" if src else ""
    if name in env.OVERRIDDEN:
        out += (". Attention : une autre valeur est définie dans le terminal (export, ~/.zshrc) et l'emporte "
                "sur backend/.env")
    return out + "."


def _elevenlabs_token(key: str, timeout: float = 8.0) -> tuple[int, dict]:
    req = urllib.request.Request(ELEVENLABS_TOKEN_URL, method="POST", data=b"",
                                 headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — adresse fixe
            return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        return e.code, body if isinstance(body, dict) else {}


async def check_transcription() -> Check:
    name = "ELEVENLABS_API_KEY"
    key = os.environ.get(name, "").strip()
    src = _source(name)
    if not key:
        detail = "définie mais vide" if name in os.environ else "absente"
        return Check(False, f"clé ElevenLabs {detail} : ajoutez {name}=… dans backend/.env, puis relancez le backend.",
                     src)
    problem = _format_problem(name, key)
    try:
        code, body = await asyncio.to_thread(_elevenlabs_token, key)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return Check(False, f"ElevenLabs injoignable ({reason}) : connexion Internet ou pare-feu ?", src)
    if code == 200:
        return Check(True, "prête", src)
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    status, message = detail.get("status") or detail.get("code") or "", detail.get("message") or ""
    if status == "missing_permissions":
        # Le jeton demande peut-être une autre permission que la transcription : on vérifie
        # sur la transcription elle-même (session ouverte puis fermée, sans audio).
        ok, why = await _probe_realtime(key)
        if ok:
            return Check(True, "prête", src)
        return Check(False, "la clé ElevenLabs n'a pas accès à la transcription : sur elevenlabs.io, "
                            "dans les réglages de la clé, autorisez « Speech to Text »" + (f" ({why})" if why else "")
                     + _where(name), src)
    if status.startswith("invalid_api_key"):
        why = problem or "clé invalide ou supprimée"
    else:
        why = problem or message or f"HTTP {code}"
    return Check(False, f"clé ElevenLabs refusée : {why}" + _where(name), src)


async def _probe_realtime(key: str) -> tuple[bool, str]:
    from websockets.asyncio.client import connect

    from .stt import URL

    try:
        async with connect(URL + "?model_id=scribe_v2_realtime&audio_format=pcm_16000",
                           additional_headers={"xi-api-key": key}, open_timeout=8, close_timeout=1) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 8))
            if first.get("message_type") == "session_started":
                return True, ""
            return False, str(first.get("error") or first.get("message_type") or "")
    except Exception as exc:  # noqa: BLE001 — diagnostic : on dit ce qui s'est passé
        return False, type(exc).__name__


async def check_llm() -> Check:
    if os.getenv("CALLHELP_LLM", "auto") == "off":
        return Check(False, "désactivés (CALLHELP_LLM=off) : seuls les conseils par mots-clés s'affichent.")
    name = "ANTHROPIC_API_KEY"
    key = os.environ.get(name, "").strip()
    src = _source(name)
    if not key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return Check(False, f"clé Anthropic absente : ajoutez {name}=… dans backend/.env, puis relancez le backend. "
                            "Les conseils par mots-clés restent actifs.", src)
    try:
        import anthropic
    except ImportError:
        return Check(False, "module anthropic non installé : dans backend, lancez "
                            ".venv/bin/pip install -e '.[dev,llm]', puis relancez le backend.", src)
    problem = _format_problem(name, key) if key else None
    try:
        client = anthropic.AsyncAnthropic(max_retries=0, timeout=8.0)
        await client.models.list(limit=1)
    except anthropic.AuthenticationError:
        why = problem or "elle a été révoquée ou n'existe pas (une clé publiée sur GitHub est révoquée automatiquement)"
        return Check(False, f"clé Anthropic refusée : {why}. Créez-en une sur console.anthropic.com" + _where(name), src)
    except anthropic.PermissionDeniedError:
        return Check(False, "accès Anthropic refusé pour cette clé (organisation ou espace de travail)" + _where(name), src)
    except anthropic.APIConnectionError:
        return Check(False, "Anthropic injoignable : connexion Internet ou pare-feu ?", src)
    except anthropic.APIStatusError as exc:
        return Check(False, f"Anthropic répond HTTP {exc.status_code} : réessayez plus tard.", src)
    return Check(True, "prêts", src)


_cache: dict | None = None
_cache_at = 0.0
_lock = asyncio.Lock()
TTL = 60.0


async def report(refresh: bool = False) -> dict:
    """État des services, mis en cache une minute (les vérifications sont gratuites, mais lentes)."""
    global _cache, _cache_at
    async with _lock:
        if env.reload_if_changed():
            refresh = True  # backend/.env modifié : nouvelles clés, nouveau diagnostic
        if refresh or _cache is None or time.monotonic() - _cache_at > TTL:
            transcription, llm = await asyncio.gather(check_transcription(), check_llm())
            _cache = {"transcription": asdict(transcription), "llm": asdict(llm),
                      "env_notes": list(env.NOTES)}
            _cache_at = time.monotonic()
        return _cache


async def log_report() -> None:
    """Au démarrage : ce qui marche et ce qui manque, en clair dans le terminal du backend."""
    try:
        r = await report(refresh=True)
    except Exception:  # noqa: BLE001 — un diagnostic ne doit jamais empêcher le démarrage
        log.exception("Diagnostic des clés")
        return
    for note in r["env_notes"]:
        log.warning("Call-help · %s", note)
    for label, item in (("Transcription en direct (ElevenLabs)", r["transcription"]),
                        ("Conseils IA et bilan (Claude)", r["llm"])):
        (log.info if item["ready"] else log.warning)("Call-help · %s : %s", label, item["message"])
