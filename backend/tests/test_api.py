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
    # Le bilan visible par l'équipe ne contient aucune émotion (Q38).
    assert "emotion" not in json.dumps(summary)


def test_messages_before_start_are_rejected():
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "transcript", "speaker": "user", "text": "hi", "start": 0, "end": 1})
        assert ws.receive_json()["type"] == "error"


def test_self_analysis_off_ignores_user_emotions(monkeypatch):
    monkeypatch.setenv("CALLHELP_LLM", "off")
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
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
        first_hint = ws.receive_json()["hint"]
        assert "Herakles" in first_hint["say"]
