from fastapi.testclient import TestClient

from app import demo_library
from app.main import app

client = TestClient(app)


def test_repo_demo_calls_are_listed_first_with_metadata():
    calls = client.get("/demo/calls").json()
    repo = [c for c in calls if c["source"] == "repo"]
    assert len(repo) >= 10 and calls[: len(repo)] == repo
    c = repo[0]
    assert c["has_audio"] and c["language"] in ("fr", "en") and c["duration"] > 60 and c["objections"]


def test_demo_call_audio_labels_and_replay():
    call_id = next(c["id"] for c in client.get("/demo/calls").json() if c["source"] == "repo")
    audio = client.get(f"/demo/calls/{call_id}/audio")
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg" and len(audio.content) > 100_000
    labels = client.get(f"/demo/calls/{call_id}/labels").json()
    replay = client.get(f"/demo/calls/{call_id}/replay").json()
    assert labels[0]["start"] >= 0 and any(e["type"] == "announcement_done" for e in replay["events"])


def test_demo_library_rejects_bad_ids():
    assert client.get("/demo/calls/../../etc/audio").status_code == 404
    assert client.get("/demo/calls/fr-9999x/labels").status_code == 404
    assert client.get("/demo/calls/fr-0054/dialogue").status_code == 404
    assert demo_library.audio_path("../secret") is None
