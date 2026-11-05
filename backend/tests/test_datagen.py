import json
import math
import struct
import wave
from array import array
from pathlib import Path

import pytest

from datagen.assemble import TurnAudio, replay_events, timeline, write_call, labels as make_labels
from datagen.dialogue import Dialogue, Turn, build_brief, validate_dialogue
from datagen.evaluate import evaluate_dir, format_report
from datagen.plan import OBJECTIONS, CallSpec, build_plan, coverage

FIXTURE = Path(__file__).parent / "fixtures" / "dialogue_fr.json"


def dialogue() -> Dialogue:
    return Dialogue.model_validate_json(FIXTURE.read_text("utf-8"))


def spec(**kw) -> CallSpec:
    base = dict(call_id="fr-9999", language="fr", sector="cabinet d'avocats", prospect_role="owner",
                prospect_personality="rushed", prospect_gender="male", seller_level="senior",
                seller_gender="female", objections=["no_time", "has_provider"],
                arc=["annoyance", "hesitation", "enthusiasm"], outcome="meeting_booked",
                target_minutes=0.9, split="train", seed=1)
    base.update(kw)
    return CallSpec(**base)


def tone(seconds: float, sr: int = 16000, freq: float = 220.0) -> bytes:
    n = int(seconds * sr)
    return array("h", (int(8000 * math.sin(2 * math.pi * freq * i / sr)) for i in range(n))).tobytes()


# --- plan ---------------------------------------------------------------------

def test_plan_is_deterministic_and_balanced():
    a, b = build_plan(200, seed=3), build_plan(200, seed=3)
    assert [s.model_dump() for s in a] == [s.model_dump() for s in b]
    cov = coverage(build_plan(1000))
    assert cov["language"] == {"en": 400, "fr": 600}
    assert cov["split"] == {"test": 100, "train": 800, "val": 100}
    counts = [cov["objection"][o] for o in OBJECTIONS]
    assert max(counts) - min(counts) < 0.1 * max(counts)
    assert len({s.call_id for s in build_plan(1000)}) == 1000


def test_plan_calls_have_no_duplicate_objection():
    assert all(len(set(s.objections)) == len(s.objections) for s in build_plan(1000))


def test_brief_mentions_constraints():
    brief = build_brief(spec())
    assert "French" in brief and "no_time, has_provider" in brief and "meeting_booked" in brief


# --- validation -----------------------------------------------------------------

def test_fixture_dialogue_is_valid():
    assert validate_dialogue(spec(), dialogue()) == []


def test_validation_catches_missing_objection_and_steps_going_back():
    d = dialogue()
    issues = validate_dialogue(spec(objections=["no_budget"]), d)
    assert any("no_budget" in i for i in issues)
    turns = list(d.turns)
    turns[-1] = turns[-1].model_copy(update={"step": "hook"})
    issues = validate_dialogue(spec(), d.model_copy(update={"turns": turns}))
    assert "les étapes reculent" in issues


def test_turn_rejects_unknown_labels():
    with pytest.raises(Exception):
        Turn(speaker="prospect", text="x", emotion="joy", intensity=0.5, step="hook",
             objection=None, pause_ms=0, overlap_ms=0)


# --- assemblage ---------------------------------------------------------------

def test_timeline_pauses_and_interruptions():
    d = dialogue()
    spans = timeline(d, [2.0] * len(d.turns))
    assert spans[0] == (0.3, 2.3)
    assert spans[1][0] == pytest.approx(2.3 + 0.2)
    i = 8  # tour qui interrompt (overlap 300 ms)
    assert spans[i][0] == pytest.approx(spans[i - 1][1] - 0.3)


def test_replay_confirms_announcement_after_notice_turn():
    d = dialogue()
    spans = timeline(d, [2.0] * len(d.turns))
    events = replay_events(make_labels(d, spans))["events"]
    kinds = [e["type"] for e in events]
    assert kinds.index("announcement_done") == 2  # après le 2e tour (annonce du vendeur)
    assert events[-1] == {"type": "stop"}
    assert {e["speaker"] for e in events if e["type"] == "transcript"} == {"user", "prospect"}


def test_stereo_mix_puts_seller_left_and_prospect_right(tmp_path):
    d = dialogue()
    audios = [TurnAudio(tone(0.5), 16000) for _ in d.turns]
    write_call(tmp_path, d, audios, telephone=False)
    with wave.open(str(tmp_path / "call.wav")) as w:
        assert w.getnchannels() == 2 and w.getframerate() == 16000
        frames = w.readframes(w.getnframes())
    samples = array("h"); samples.frombytes(frames)
    left, right = samples[0::2], samples[1::2]
    spans = timeline(d, [0.5] * len(d.turns))
    s0, e0 = spans[0]  # tour du vendeur : signal à gauche seulement
    mid = int((s0 + e0) / 2 * 16000)
    assert abs(left[mid]) + abs(left[mid + 7]) > 0 and right[mid] == 0
    labels = json.loads((tmp_path / "labels.json").read_text())
    assert labels[2]["objection"] == "no_time" and labels[2]["speaker"] == "prospect"


def test_telephone_version_degrades_prospect_channel_only(tmp_path):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg absent")
    d = dialogue()
    # Tonalité à 220 Hz : sous la coupure téléphonique de 300 Hz.
    write_call(tmp_path, d, [TurnAudio(tone(0.4), 16000) for _ in d.turns])

    def channels(path):
        with wave.open(str(path)) as w:
            assert w.getnchannels() == 2 and w.getframerate() == 16000
            s = array("h"); s.frombytes(w.readframes(w.getnframes()))
        return s[0::2], s[1::2]

    def rms(x):
        return (sum(v * v for v in x) / max(1, len(x))) ** 0.5

    left, right = channels(tmp_path / "call.wav")
    tel_left, tel_right = channels(tmp_path / "call_telephone.wav")
    assert rms(tel_left) == pytest.approx(rms(left), rel=0.01)   # vendeur intact (micro large bande)
    assert rms(tel_right) < 0.5 * rms(right)                     # prospect filtré 300-3 400 Hz


def test_labels_flag_turns_without_acoustic_cue(tmp_path):
    turns = write_call(tmp_path, dialogue(), None)
    by_text = {t.text: t for t in turns}
    assert by_text["Jeudi, ça marche."].acoustic_cue is True  # indication « warm » envoyée
    assert any(not t.acoustic_cue for t in turns)


# --- évaluation -----------------------------------------------------------------

def test_evaluation_on_fixture(tmp_path):
    write_call(tmp_path / "fr-9999", dialogue(), None)
    report = evaluate_dir(tmp_path)
    assert report["calls"] == 1
    # « j'ai pas le temps » et « on bosse déjà avec une agence » : le premier est
    # reconnu par mots-clés, le second non (formulation libre) -> rappel 50 %.
    assert report["objections"]["no_time"]["recall"] == 1.0
    assert report["objections"]["has_provider"]["recall"] == 0.0
    assert 0 < report["step_accuracy"] <= 1
    assert "Objection" in format_report(report)
