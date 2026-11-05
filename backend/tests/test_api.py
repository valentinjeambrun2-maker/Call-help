import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_scenarios():
    assert client.get("/health").json() == {"status": "ok"}
    assert any(s["id"] == "cold_call_fr" for s in client.get("/scenarios").json())


def test_full_demo_call_over_websocket(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    script = json.loads((Path(__file__).parent.parent / "demo" / "cold_call_demo.json").read_text())
    received = []
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "scenario_id": "cold_call_fr"})
        for event in script["events"]:
            ws.send_json(event)
            if event["type"] == "stop":
                break
        while True:
            msg = ws.receive_json()
            received.append(msg)
            if msg["type"] == "summary":
                break

    hints = [m["hint"] for m in received if m["type"] == "hint"]
    kinds = {h["kind"] for h in hints}
    assert {"step", "objection", "emotion"} <= kinds
    summary = received[-1]["summary"]
    assert summary["steps_reached"] == ["hook", "qualify", "value", "close"]
    assert summary["objections"] == ["has_provider"]
    # Le bilan visible par l'équipe ne contient aucun état émotionnel (Q38).
    dumped = json.dumps(summary)
    for state in ("interest", "hesitation", "annoyance", "boredom", "enthusiasm"):
        assert state not in dumped
    # Trace de conformité : le message d'information a été dit à 0 s.
    assert summary["announcement_at"] == 0
    assert hints[0]["kind"] == "announcement"


def test_messages_before_start_are_rejected():
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "transcript", "speaker": "user", "text": "hi", "start": 0, "end": 1})
        assert ws.receive_json()["type"] == "error"


def test_self_analysis_off_ignores_user_emotions(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "self_analysis": False})
        ws.receive_json(); ws.receive_json()  # message d'information + état
        ws.send_json({"type": "announcement_done", "ts": 0})
        ws.receive_json(); ws.receive_json()  # conseil d'accroche + état
        ws.send_json({"type": "voice", "speaker": "user", "ts": 40,
                      "scores": {"interest": 0, "hesitation": 1, "annoyance": 0, "boredom": 0, "enthusiasm": 0}})
        state = ws.receive_json()
        assert [e["speaker"] for e in state["emotions"]] == ["prospect"]


def test_demo_endpoint_and_start_context(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    demo = client.get("/demo").json()
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "context": demo["context"]})
        announcement = ws.receive_json()["hint"]
        assert announcement["kind"] == "announcement" and "assistant" in announcement["say"]
        assert ws.receive_json()["announcement_pending"] is True
        ws.send_json({"type": "announcement_done", "ts": 2})
        hook = ws.receive_json()["hint"]
        assert "Herakles" in hook["say"]
        assert ws.receive_json()["announcement_pending"] is False


def test_emotions_ignored_until_announcement(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    hesitant = {"interest": 0, "hesitation": 1, "annoyance": 0, "boredom": 0, "enthusiasm": 0}
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json(); ws.receive_json()
        for ts in range(0, 50, 5):
            ws.send_json({"type": "voice", "speaker": "prospect", "ts": ts, "scores": hesitant})
            state = ws.receive_json()
        assert state["announcement_pending"] is True
        assert all(e["state"] is None and not e["calibrated"] for e in state["emotions"])
        ws.send_json({"type": "stop"})
        summary = ws.receive_json()["summary"]
        assert summary["announcement_at"] is None and summary["emotion_analysis_used"] is False


def test_self_analysis_on_by_default(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        state = ws.receive_json()
        assert [e["speaker"] for e in state["emotions"]] == ["prospect", "user"]
