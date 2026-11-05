from app.metrics import ConversationMetrics, is_question
from app.models import Speaker


def test_is_question():
    assert is_question("Comment gérez-vous ça")
    assert is_question("Vous avez un site ?")
    assert not is_question("Je vous propose jeudi.")


def test_wpm_and_talk_ratio():
    m = ConversationMetrics()
    m.add_segment(Speaker.USER, " ".join(["mot"] * 30), 0, 10)  # 180 mots/min
    m.add_segment(Speaker.PROSPECT, " ".join(["mot"] * 10), 10, 20)
    snap = m.snapshot(20)
    assert snap.user_wpm == 180.0
    assert snap.user_talk_ratio == 0.5


def test_monologue_resets_when_prospect_speaks():
    m = ConversationMetrics()
    m.add_segment(Speaker.USER, "a b c", 0, 20)
    m.add_segment(Speaker.USER, "d e f", 21, 50)  # pause < 1,5 s : même monologue
    assert m.current_monologue() == 50
    m.add_segment(Speaker.PROSPECT, "ok", 51, 52)
    assert m.current_monologue() == 0
