import pytest

from crowdpanel.loader import ConfigError, default_questions, parse_personas, parse_scenario


def test_example_scenario_loads(scenario):
    assert scenario.brand_name == "Ridgeway Roasters"
    assert [o.id for o in scenario.offers] == ["a", "b"]
    assert scenario.events[0].day == 2


def test_example_personas_load(personas):
    assert len(personas) == 10
    jennifer = personas[0]
    assert jennifer.active_hours == [6, 7, 8, 9]
    assert jennifer.handle() == "@jennifer_miller"


def test_scenario_requires_offers():
    with pytest.raises(ConfigError):
        parse_scenario({"title": "x", "platform": "y", "context": "z", "offers": []})


def test_choice_question_needs_options():
    with pytest.raises(ConfigError):
        parse_scenario({
            "title": "x", "platform": "y", "context": "z",
            "offers": [{"description": "d"}],
            "questions": [{"id": "q", "text": "?", "type": "choice"}],
        })


def test_offer_ids_default_to_letters():
    s = parse_scenario({"title": "x", "platform": "y", "context": "z",
                        "offers": [{"description": "one"}, {"description": "two"}]})
    assert [o.id for o in s.offers] == ["a", "b"]


def test_duplicate_persona_names_rejected():
    with pytest.raises(ConfigError):
        parse_personas([{"name": "Ana", "profile": "x"}, {"name": "ana", "profile": "y"}])


def test_activity_range_checked():
    with pytest.raises(ConfigError):
        parse_personas([{"name": "Ana", "profile": "x", "activity": 2}])


def test_default_questions_depend_on_offer_count(scenario):
    two = default_questions(scenario.offers)
    one = default_questions(scenario.offers[:1])
    assert two[0].id == "preferred_offer" and "None of them" in two[0].options
    assert one[0].id == "would_buy"
