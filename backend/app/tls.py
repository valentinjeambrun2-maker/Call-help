"""Contexte TLS pour les connexions directes (transcription ElevenLabs, diagnostic des clés).

Le Python téléchargé sur python.org pour Mac n'a aucun certificat racine tant qu'on n'a pas
lancé « Install Certificates.command » : toute connexion sécurisée échoue alors avec
« CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate ». Le SDK d'Anthropic ne
l'est pas : il passe par le magasin de certificats du système (truststore). On fait de même :
- truststore s'il est installé : trousseau macOS, magasin Windows ou celui du système Linux
  (il suit aussi les certificats d'un proxy d'entreprise installés sur la machine) ;
- sinon, les certificats par défaut complétés par ceux de certifi.
"""

from __future__ import annotations

import functools
import ssl
import urllib.error


@functools.lru_cache(maxsize=1)
def client_context() -> ssl.SSLContext:
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 — absent ou inutilisable : repli
        pass
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    return ctx


CERT_HELP = ("certificats de sécurité absents dans le Python de ce Mac : lancez « Install Certificates.command » "
             "(dossier Applications > Python 3.x), puis relancez le backend")


def is_cert_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return True
        nxt = exc.__cause__ or exc.__context__
        if nxt is None and isinstance(exc, urllib.error.URLError):
            nxt = exc.reason  # urllib emballe l'erreur SSL dans .reason
        if not isinstance(nxt, BaseException):
            return False
        exc = nxt
    return False
