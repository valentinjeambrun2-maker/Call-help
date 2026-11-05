"""Diagnostic des clés : dire pourquoi la transcription ou les conseils IA ne marchent pas
(cas réel : « la transcription ne marche pas dans la démo », sans autre indice)."""

import os
import urllib.error

import pytest

from app import diagnostics, env
from app.session import CallSession

GOOD_EL = "sk_" + "a" * 48  # 51 caractères, comme une vraie clé ElevenLabs


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("ELEVENLABS_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CALLHELP_LLM"):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch
    env.load_env(env.ENV_FILE)  # retire ce que les fichiers de test ont chargé (fichier absent en test)


def test_env_file_saved_as_rtf_by_textedit_is_still_read_and_reported(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text("{\\rtf1\\ansi\\ansicpg1252\\cocoartf2761\n{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
                 "\\f0\\fs24 \\cf0 ELEVENLABS_API_KEY=" + GOOD_EL + "\\\nANTHROPIC_API_KEY=sk-ant-x}\n")
    env.load_env(f)
    assert os.environ["ELEVENLABS_API_KEY"] == GOOD_EL and os.environ["ANTHROPIC_API_KEY"] == "sk-ant-x"
    assert any("RTF" in n for n in env.NOTES)


def test_env_tolerates_export_quotes_spaces_and_bom(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text('﻿export ELEVENLABS_API_KEY = "' + GOOD_EL + '"\r\n# commentaire\r\nANTHROPIC_API_KEY=\'k\'\r\n')
    env.load_env(f)
    assert os.environ["ELEVENLABS_API_KEY"] == GOOD_EL and os.environ["ANTHROPIC_API_KEY"] == "k"
    assert env.FROM_FILE == {"ELEVENLABS_API_KEY", "ANTHROPIC_API_KEY"}


def test_env_saved_as_env_txt_with_smart_quotes_is_still_read(tmp_path, clean_env):
    (tmp_path / ".env.txt").write_text("ELEVENLABS_API_KEY=“" + GOOD_EL + "”\n")
    env.load_env(tmp_path / ".env")
    assert os.environ["ELEVENLABS_API_KEY"] == GOOD_EL and any("renommez" in n for n in env.NOTES)


def test_edited_env_file_is_reread_and_a_removed_key_disappears(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}\nANTHROPIC_API_KEY=sk-ant-old\n")
    env.load_env(f)
    assert not env.reload_if_changed(f)  # inchangé : pas relu
    f.write_text("ANTHROPIC_API_KEY=sk-ant-new-and-longer\n")
    assert env.reload_if_changed(f)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-new-and-longer" and "ELEVENLABS_API_KEY" not in os.environ


def test_a_key_exported_in_the_terminal_wins_and_is_flagged(tmp_path, clean_env):
    clean_env.setenv("ELEVENLABS_API_KEY", "sk_old")
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}\n")
    env.load_env(f)
    assert os.environ["ELEVENLABS_API_KEY"] == "sk_old" and "ELEVENLABS_API_KEY" in env.OVERRIDDEN
    assert "l'emporte" in diagnostics._where("ELEVENLABS_API_KEY")


async def test_missing_elevenlabs_key_says_where_to_put_it(clean_env):
    check = await diagnostics.check_transcription()
    assert not check.ready and "absente" in check.message and "backend/.env" in check.message


async def test_key_one_character_too_long_is_explained(clean_env, tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}x\n")
    env.load_env(f)
    clean_env.setattr(diagnostics, "_elevenlabs_token", lambda key: (400, {"detail": {
        "status": "invalid_api_key_length", "message": "API key must be exactly 51 characters, got 52."}}))
    check = await diagnostics.check_transcription()
    assert not check.ready and "52 caractères au lieu de 51" in check.message and "backend/.env" in check.message
    assert GOOD_EL not in check.message  # jamais la clé elle-même


async def test_valid_key_is_ready(clean_env):
    clean_env.setenv("ELEVENLABS_API_KEY", GOOD_EL)
    clean_env.setattr(diagnostics, "_elevenlabs_token", lambda key: (200, {}))
    assert (await diagnostics.check_transcription()).ready


async def test_key_without_speech_to_text_permission_is_explained(clean_env):
    clean_env.setenv("ELEVENLABS_API_KEY", GOOD_EL)
    clean_env.setattr(diagnostics, "_elevenlabs_token", lambda key: (401, {"detail": {
        "status": "missing_permissions", "message": "missing the permission speech_to_text"}}))

    async def probe(key):
        return False, "You must be authenticated to use this endpoint."
    clean_env.setattr(diagnostics, "_probe_realtime", probe)
    check = await diagnostics.check_transcription()
    assert not check.ready and "Speech to Text" in check.message


async def test_restricted_key_that_can_still_transcribe_is_ready(clean_env):
    """Clé restreinte : le jeton est refusé faute d'une autre permission, la transcription marche."""
    clean_env.setenv("ELEVENLABS_API_KEY", GOOD_EL)
    clean_env.setattr(diagnostics, "_elevenlabs_token", lambda key: (401, {"detail": {"status": "missing_permissions"}}))

    async def probe(key):
        return True, ""
    clean_env.setattr(diagnostics, "_probe_realtime", probe)
    assert (await diagnostics.check_transcription()).ready


async def test_unreachable_elevenlabs_is_not_reported_as_a_bad_key(clean_env):
    clean_env.setenv("ELEVENLABS_API_KEY", GOOD_EL)

    def offline(key):
        raise urllib.error.URLError("nodename nor servname provided")
    clean_env.setattr(diagnostics, "_elevenlabs_token", offline)
    check = await diagnostics.check_transcription()
    assert not check.ready and "injoignable" in check.message


async def test_revoked_anthropic_key_is_explained(clean_env):
    import anthropic
    import httpx2

    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-revoked")
    real = anthropic.AsyncAnthropic

    def refuse(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                      "message": "invalid x-api-key"}})

    def client(**kw):
        return real(**kw, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(refuse)))
    clean_env.setattr(anthropic, "AsyncAnthropic", client)
    check = await diagnostics.check_llm()
    assert not check.ready and "clé Anthropic refusée" in check.message and "révoquée" in check.message


async def test_llm_switched_off_is_said(clean_env):
    clean_env.setenv("CALLHELP_LLM", "off")
    check = await diagnostics.check_llm()
    assert not check.ready and "CALLHELP_LLM=off" in check.message


async def test_refused_key_during_a_call_gives_the_diagnosed_reason_once(monkeypatch):
    out = []

    async def send(m):
        out.append(m)

    async def report(refresh=False):
        return {"transcription": {"ready": False, "message": "clé ElevenLabs refusée : elle fait 52 caractères au lieu de 51."}}
    monkeypatch.setattr(diagnostics, "report", report)
    s = CallSession(send)
    await s.handle({"type": "start"})
    for _ in range(2):  # les deux canaux tombent
        await s._on_stt_status("error", "transcription arrêtée : clé ElevenLabs refusée")
    errors = [m["message"] for m in out if m["type"] == "error"]
    assert errors == ["Transcription arrêtée : clé ElevenLabs refusée : elle fait 52 caractères au lieu de 51."]


def test_status_endpoint_serves_the_report_without_keys(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    async def report(refresh=False):
        return {"transcription": {"ready": True, "message": "prête", "source": "backend/.env"},
                "llm": {"ready": False, "message": "clé Anthropic absente", "source": None}, "env_notes": []}
    monkeypatch.setattr(diagnostics, "report", report)
    r = TestClient(app).get("/status").json()
    assert r["transcription"]["ready"] and not r["llm"]["ready"]


# --- Python de python.org sur Mac : aucun certificat (cas réel, « CERTIFICATE_VERIFY_FAILED ») ---

def test_tls_context_has_root_certificates_even_when_python_has_none(monkeypatch, tmp_path):
    import sys

    from app import tls

    empty = tmp_path / "empty.pem"
    empty.write_text("")
    monkeypatch.setenv("SSL_CERT_FILE", str(empty))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "truststore", None)  # repli certifi
    tls.client_context.cache_clear()
    try:
        assert tls.client_context().cert_store_stats()["x509_ca"] > 50
    finally:
        tls.client_context.cache_clear()


async def test_certificate_error_is_explained_not_reported_as_a_network_problem(clean_env):
    import ssl

    clean_env.setenv("ELEVENLABS_API_KEY", GOOD_EL)

    def no_certs(key):
        raise urllib.error.URLError(ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"))
    clean_env.setattr(diagnostics, "_elevenlabs_token", no_certs)
    check = await diagnostics.check_transcription()
    assert not check.ready and "Install Certificates" in check.message and "injoignable" not in check.message
