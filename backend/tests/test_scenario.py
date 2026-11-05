from app.scenario import ScenarioTracker, contains_keyword, fill, load_scenario, normalize

import pytest


def test_normalize_strips_accents_and_apostrophes():
    assert normalize("Déjà’Été") == "deja'ete"


def test_keyword_matches_whole_words_only():
    assert contains_keyword("Oui, ok pour moi", "ok")
    assert not contains_keyword("Je lis un book", "ok")
    assert contains_keyword("On a DEJA un prestataire", "déjà un prestataire")


def test_fill_shows_readable_label_for_missing_values():
    assert fill("{company} aide {segment}", {"company": "ACME"}) == "ACME aide [type de clients]"
    assert fill("{user_name} from {company}", {}, "en") == "[your name] from [your company]"
    assert fill("{inconnu}", {}) == "[inconnu]"


def test_invalid_scenario_id_rejected():
    with pytest.raises(ValueError):
        load_scenario("../secrets")


def test_tracker_detects_objection_without_advancing():
    t = ScenarioTracker(load_scenario("cold_call_fr"))
    objection, advanced = t.on_prospect_text("Désolé, je n'ai pas le temps", 5)
    assert objection.id == "no_time" and not advanced
    assert t.step.id == "hook"
    assert t.state().active_objection == "Pas le temps"


def test_tracker_advances_on_exit_keyword_and_stops_at_last_step():
    t = ScenarioTracker(load_scenario("cold_call_fr"))
    for ts in range(10):
        t.advance(ts)
    assert t.step.id == "close"
    assert t.steps_reached == ["hook", "qualify", "value", "close"]


def test_step_overtime():
    t = ScenarioTracker(load_scenario("cold_call_fr"))
    assert not t.step_overtime(10)
    assert t.step_overtime(31)
