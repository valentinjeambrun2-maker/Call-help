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


# --- Revue du diagnostic (6 défauts confirmés) ---------------------------------------------

@pytest.mark.parametrize("value", ["…", "…" + GOOD_EL, GOOD_EL[:20] + "\u200b" + GOOD_EL[20:], GOOD_EL[:20] + "’" + GOOD_EL[21:]])
async def test_invisible_or_typographic_character_is_explained_without_crashing(clean_env, tmp_path, value):
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={value}\n", encoding="utf-8")
    env.load_env(f)

    def must_not_call(key):
        raise AssertionError("aucune requête avec une clé impossible à envoyer")
    clean_env.setattr(diagnostics, "_elevenlabs_token", must_not_call)
    check = await diagnostics.check_transcription()
    assert not check.ready and "invisible ou typographique" in check.message


async def test_one_crashing_check_does_not_hide_the_other_or_break_status(clean_env):
    async def boom():
        raise UnicodeEncodeError("latin-1", "…", 0, 1, "x")

    async def fine():
        return diagnostics.Check(True, "prêts")
    clean_env.setattr(diagnostics, "check_transcription", boom)
    clean_env.setattr(diagnostics, "check_llm", fine)
    r = await diagnostics.report(refresh=True)
    assert not r["transcription"]["ready"] and "terminal" in r["transcription"]["message"] and r["llm"]["ready"]


def test_inline_comment_is_not_part_of_the_key(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}  # clé d’essai\n", encoding="utf-8")
    env.load_env(f)
    assert os.environ["ELEVENLABS_API_KEY"] == GOOD_EL


def test_key_listed_twice_uses_the_last_non_empty_value_and_says_so(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text("ANTHROPIC_API_KEY=\nELEVENLABS_API_KEY=\n# ajoutée en bas\nELEVENLABS_API_KEY=" + GOOD_EL + "\n")
    env.load_env(f)
    assert os.environ["ELEVENLABS_API_KEY"] == GOOD_EL and not env.OVERRIDDEN
    assert any("apparaît 2 fois" in n for n in env.NOTES)
    env.reload_if_changed(f)  # même résultat au démarrage et à la relecture
    assert os.environ["ELEVENLABS_API_KEY"] == GOOD_EL


async def test_empty_terminal_variable_masking_the_file_is_named(tmp_path, clean_env):
    clean_env.setenv("ELEVENLABS_API_KEY", "")
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}\n")
    env.load_env(f)
    check = await diagnostics.check_transcription()
    assert not check.ready and "masque la clé" in check.message and "unset ELEVENLABS_API_KEY" in check.message


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_elevenlabs_outage_is_not_reported_as_a_refused_key(clean_env, code):
    clean_env.setenv("ELEVENLABS_API_KEY", GOOD_EL)
    clean_env.setattr(diagnostics, "_elevenlabs_token", lambda key: (code, {}))
    check = await diagnostics.check_transcription()
    assert not check.ready and f"HTTP {code}" in check.message and "refusée" not in check.message


async def test_diagnosis_is_redone_when_a_call_rereads_the_env_file(tmp_path, clean_env):
    f = tmp_path / ".env"
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}x\n")
    env.load_env(f)
    clean_env.setattr(diagnostics, "_elevenlabs_token", lambda key: (200, {}) if len(key) == 51 else (400, {"detail": {
        "status": "invalid_api_key_length"}}))
    assert "52 caractères" in (await diagnostics.report(refresh=True))["transcription"]["message"]
    f.write_text(f"ELEVENLABS_API_KEY={GOOD_EL}\n")
    env.reload_if_changed(f)  # relu au début d'un appel, pas par report()
    assert (await diagnostics.report())["transcription"]["ready"]


async def test_exhausted_anthropic_credits_are_reported_and_stop_retrying(clean_env):
    import anthropic
    import httpx2

    from app.coach.providers import ClaudeProvider, CoachContext

    calls = []

    def no_credit(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={"data": [], "has_more": False, "first_id": None, "last_id": None})
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message":
                               "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing."}})
    real = anthropic.AsyncAnthropic

    def client(**kw):
        kw.pop("http_client", None)
        return real(**{"api_key": "sk-ant-test", **kw}, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(no_credit)))
    clean_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    clean_env.setattr(anthropic, "AsyncAnthropic", client)
    provider = ClaudeProvider()
    provider.client = client(max_retries=0)
    ctx = CoachContext(language="fr", scenario_title="t", step_title="s", step_goal="g", objection=None,
                       prospect_emotion=None, metrics={}, recent_transcript=[])
    try:
        for _ in range(3):
            assert await provider.suggest(ctx) is None
        assert sum(p.endswith("/messages") for p in calls) == 1  # pas de nouvel essai à chaque tour
        assert "crédits Anthropic épuisés" in provider.disabled_reason
        check = await diagnostics.check_llm()
        assert not check.ready and "crédits Anthropic épuisés" in check.message
    finally:
        diagnostics.note_llm_issue(None)


async def test_call_with_an_unusable_key_says_why_at_once(monkeypatch):
    out = []

    async def send(m):
        out.append(m)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "…" + GOOD_EL)
    s = CallSession(send)
    await s.handle({"type": "start", "transcription": "server"})
    assert s.stt is None
    assert any(m["type"] == "error" and "invisible ou typographique" in m["message"] for m in out)
