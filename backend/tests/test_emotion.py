from app.emotion import EmotionFusion
from app.models import EmotionState as E, FaceMsg, Speaker

NEUTRAL = {E.INTEREST: 0.2, E.HESITATION: 0.2, E.ANNOYANCE: 0.1, E.BOREDOM: 0.1, E.ENTHUSIASM: 0.2}
HESITANT = {E.INTEREST: 0.1, E.HESITATION: 0.95, E.ANNOYANCE: 0.2, E.BOREDOM: 0.1, E.ENTHUSIASM: 0.0}


def calibrated() -> EmotionFusion:
    f = EmotionFusion()
    f.start(0)
    for ts in range(0, 30, 2):
        f.add_voice(Speaker.PROSPECT, NEUTRAL, ts)
    return f


def test_nothing_shown_before_calibration():
    f = EmotionFusion()
    f.start(0)
    f.add_voice(Speaker.PROSPECT, HESITANT, 5)
    assert f.reading(Speaker.PROSPECT, 5).state is None


def test_nothing_analysed_before_start():
    f = EmotionFusion()
    for ts in range(0, 60):
        f.add_voice(Speaker.PROSPECT, HESITANT, ts)
    r = f.reading(Speaker.PROSPECT, 60)
    assert r.state is None and not r.calibrated
    assert f.people[Speaker.PROSPECT].voice.current == {}


def test_calibration_window_starts_at_each_channel_first_sample():
    f = EmotionFusion()
    f.start(100)
    f.add_voice(Speaker.PROSPECT, NEUTRAL, 110)
    assert not f.reading(Speaker.PROSPECT, 131).calibrated
    assert f.reading(Speaker.PROSPECT, 141).calibrated


def test_late_channel_is_calibrated_not_shown_raw():
    """Revue : une voix arrivée après 30 s s'affichait en score brut « sûr »."""
    grave = {E.INTEREST: 0.1, E.HESITATION: 0.1, E.ANNOYANCE: 0.8, E.BOREDOM: 0.1, E.ENTHUSIASM: 0.1}
    f = EmotionFusion()
    f.start(0)
    for ts in range(31, 41):
        f.add_voice(Speaker.PROSPECT, grave, ts)
    assert f.reading(Speaker.PROSPECT, 40).state is None  # encore en calibration
    for ts in range(41, 70):
        f.add_voice(Speaker.PROSPECT, grave, ts)
    assert f.reading(Speaker.PROSPECT, 70).state is None  # profil constant neutralisé


def test_samples_before_notice_are_rejected():
    """Revue : un échantillon horodaté avant le message d'information était analysé."""
    f = EmotionFusion()
    f.start(20)
    f.add_voice(Speaker.PROSPECT, HESITANT, 5)
    assert f.people[Speaker.PROSPECT].voice.current == {} and f.samples_used == 0


def test_strong_signal_shown_after_calibration():
    f = calibrated()
    for ts in range(31, 40):
        f.add_voice(Speaker.PROSPECT, HESITANT, ts)
    r = f.reading(Speaker.PROSPECT, 40)
    assert r.state is E.HESITATION and r.label == "sûr" and r.confidence >= 0.7


def test_baseline_neutralises_a_constant_trait():
    f = calibrated()
    for ts in range(31, 40):
        f.add_voice(Speaker.PROSPECT, NEUTRAL, ts)
    assert f.reading(Speaker.PROSPECT, 40).state is None


def test_face_not_visible_falls_back_on_voice():
    f = calibrated()
    f.add_face(FaceMsg(speaker=Speaker.PROSPECT, ts=35, visible=False))
    for ts in range(31, 40):
        f.add_voice(Speaker.PROSPECT, HESITANT, ts)
    r = f.reading(Speaker.PROSPECT, 40)
    assert r.face_visible is False and r.state is E.HESITATION


def test_pause_stops_analysis():
    f = calibrated()
    f.paused = True
    f.add_voice(Speaker.PROSPECT, HESITANT, 35)
    assert f.reading(Speaker.PROSPECT, 40).state is None
