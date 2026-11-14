import json

from crowdpanel.engines import EngineError, get_engine
from crowdpanel.engines.base import Engine, Result, Usage
from crowdpanel.report import build_report, compare_markdown
from crowdpanel.simulation import Runner, create_simulation, estimate, load_simulation, round_clock
from crowdpanel.survey import aggregate, run_survey
from crowdpanel.world import World


def test_round_clock():
    assert round_clock(0, 8, 22) == (1, 8)
    assert round_clock(14, 8, 22) == (1, 22)
    assert round_clock(15, 8, 22) == (2, 8)


def test_estimate_counts_only_active_hours(personas):
    est = estimate(personas, 1, 8, 22)
    assert 0 < est["activations"] < len(personas) * 15
    assert est["calls"] == est["activations"] + 2 * len(personas) + 1
    assert estimate(personas, 1, 8, 22, baseline=False)["calls"] == est["activations"] + len(personas) + 1


async def test_full_run_with_fake_engine(db, scenario, personas):
    engine = get_engine("fake", seed=3)
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=11)
    status = await Runner(db, engine).run(sim_id)
    assert status == "done"
    sim = db.simulation(sim_id)
    assert sim["current_round"] == sim["total_rounds"] == 15
    assert db.one("SELECT COUNT(*) AS n FROM posts WHERE simulation_id = ? AND kind = 'announcement'", (sim_id,))["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM activations WHERE simulation_id = ?", (sim_id,))["n"] > 0

    await run_survey(db, engine, sim_id)
    agg = aggregate(db, sim_id)
    assert agg["respondents"] == len(personas)
    choice = agg["questions"][0]
    assert sum(choice["overall"].values()) == len(personas)

    report = await build_report(db, engine, sim_id)
    assert "## Survey" in report and "Run details" in report


async def test_memory_stays_bounded(db, scenario, personas):
    engine = get_engine("fake", seed=5)
    sim_id = create_simulation(db, scenario, personas, None, engine, days=2, seed=2)
    await Runner(db, engine).run(sim_id)
    for row in db.q("SELECT notes_json FROM personas WHERE simulation_id = ?", (sim_id,)):
        assert len(json.loads(row["notes_json"])) <= 8


async def test_call_budget_pauses_and_resume_finishes(db, scenario, personas):
    engine = get_engine("fake", seed=1)
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=4, max_calls=5)
    assert await Runner(db, engine).run(sim_id) == "paused"
    sim = db.simulation(sim_id)
    assert sim["status"] == "paused" and "call limit" in sim["error"]
    assert sim["current_round"] < sim["total_rounds"]

    db.run("UPDATE simulations SET max_calls = NULL WHERE id = ?", (sim_id,))
    db.commit()
    assert await Runner(db, engine).run(sim_id) == "done"
    assert db.simulation(sim_id)["current_round"] == 15


async def test_stop_request_pauses(db, scenario, personas):
    engine = get_engine("fake")
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=4)
    db.set_status(sim_id, "stopping")
    # Runner sets "running" at start, so mimic a stop that arrives mid-run
    runner = Runner(db, engine)
    original = runner._seed

    def seed_then_stop(*a, **k):
        original(*a, **k)
        db.set_status(sim_id, "stopping")

    runner._seed = seed_then_stop
    assert await runner.run(sim_id) == "paused"
    assert db.simulation(sim_id)["error"] == "stopped by user"


class BrokenEngine(Engine):
    name = "broken"

    async def generate(self, system, prompt, schema=None, max_tokens=4000):
        raise EngineError("not logged in", retryable=False)


async def test_fatal_engine_error_fails_loudly(db, scenario, personas):
    engine = BrokenEngine("x")
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=4)
    assert await Runner(db, engine).run(sim_id) == "failed"
    sim = db.simulation(sim_id)
    assert sim["status"] == "failed" and "not logged in" in sim["error"]
    assert db.one("SELECT COUNT(*) AS n FROM calls WHERE simulation_id = ? AND error IS NOT NULL", (sim_id,))["n"] >= 1


class ScriptedEngine(Engine):
    """Every persona comments on the announcement and follows the first persona."""

    name = "scripted"

    async def generate(self, system, prompt, schema=None, max_tokens=4000):
        data = {
            "thought": "t",
            "opinion": "Too pricey for me.",
            "memory_note": "Saw the launch.",
            "actions": [
                {"type": "comment", "post_id": 1, "reply_to": None, "handle": None, "content": "How fresh is it?"},
                {"type": "like", "post_id": 1, "reply_to": None, "handle": None, "content": None},
                {"type": "like", "post_id": 999, "reply_to": None, "handle": None, "content": None},
                {"type": "follow", "post_id": None, "reply_to": None, "handle": "@jennifer_miller", "content": None},
            ],
        }
        return Result(text="", data=data, usage=Usage(input_tokens=10, output_tokens=5))


async def test_actions_are_validated(db, scenario, personas):
    engine = ScriptedEngine("x")
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=9, max_actions=3)
    await Runner(db, engine).run(sim_id)
    # the like on a post that does not exist is dropped; only 3 actions are allowed per turn
    assert db.one("SELECT COUNT(*) AS n FROM likes WHERE post_id = 999")["n"] == 0
    likes = db.one("SELECT COUNT(DISTINCT persona_id) AS n FROM likes WHERE simulation_id = ?", (sim_id,))["n"]
    assert likes == db.one("SELECT COUNT(DISTINCT persona_id) AS n FROM activations WHERE simulation_id = ?", (sim_id,))["n"]
    assert db.one("SELECT COUNT(*) AS n FROM follows WHERE simulation_id = ?", (sim_id,))["n"] == 0
    stance = db.one("SELECT stance FROM personas WHERE simulation_id = ? AND activations > 0", (sim_id,))["stance"]
    assert stance == "Too pricey for me."


async def test_feed_hides_own_posts_and_shows_pinned(db, scenario, personas):
    engine = get_engine("fake")
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=1)
    _, _, _, ps = load_simulation(db, sim_id)
    world = World(db, sim_id, ps)
    world.add_post(0, 1, 8, None, "Ridgeway Roasters", "launch", kind="announcement")
    mine = world.add_post(1, 1, 9, ps[0], ps[0].name, "my own post")
    other = world.add_post(1, 1, 9, ps[1], ps[1].name, "someone else")
    import random
    feed, shown = world.build_feed(ps[0], 2, 5, random.Random(1))
    assert "PINNED" in feed
    assert other in shown and mine not in shown


async def test_ab_comparison(db, scenario, personas):
    from crowdpanel.loader import load_personas
    from tests.conftest import EXAMPLE

    engine = get_engine("fake", seed=8)
    ids = []
    for offer in ("a", "b"):
        ps = load_personas(EXAMPLE / "personas.yaml")
        sim_id = create_simulation(db, scenario, ps, [offer], engine, days=1, seed=21)
        await Runner(db, engine).run(sim_id)
        await run_survey(db, engine, sim_id)
        ids.append(sim_id)
    text = compare_markdown(db, *ids)
    assert "| Option | A | B |" in text


async def test_first_impressions_are_taken_alone_and_compared(db, scenario, personas):
    from crowdpanel.survey import opinion_shifts

    engine = get_engine("fake", seed=13)
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=6)
    await Runner(db, engine).run(sim_id)
    before = db.q("SELECT * FROM survey WHERE simulation_id = ? AND phase = 'before'", (sim_id,))
    assert len(before) == len(personas)
    # first impressions are asked before any persona acts
    first_activation = db.one("SELECT MIN(id) AS n FROM calls WHERE simulation_id = ? AND purpose = 'activation'", (sim_id,))["n"]
    last_baseline = db.one("SELECT MAX(id) AS n FROM calls WHERE simulation_id = ? AND purpose = 'survey-before'", (sim_id,))["n"]
    assert last_baseline < first_activation

    await run_survey(db, engine, sim_id)
    shifts = opinion_shifts(db, sim_id)
    assert all(s["changes"] for s in shifts)
    report = await build_report(db, engine, sim_id)
    assert "Before the discussion" in report and "Who changed their answer" in report


async def test_baseline_can_be_skipped(db, scenario, personas):
    engine = get_engine("fake")
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=6, baseline=False)
    await Runner(db, engine).run(sim_id)
    assert db.one("SELECT COUNT(*) AS n FROM survey WHERE simulation_id = ?", (sim_id,))["n"] == 0


class IdleThenActiveEngine(Engine):
    name = "idle"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.turn = 0

    async def generate(self, system, prompt, schema=None, max_tokens=4000):
        self.turn += 1
        if "SURVEY" in prompt:
            from crowdpanel.engines.fake import FakeEngine
            return await FakeEngine().generate(system, prompt, schema)
        act = self.turn % 3 == 0
        actions = [{"type": "comment" if act else "nothing", "post_id": 1, "reply_to": None, "handle": None,
                    "content": "A real point." if act else None}]
        note = "Someone compared prices." if act else "Nothing new in the feed."
        return Result(text="", data={"thought": "", "opinion": "ok", "memory_note": note, "actions": actions})


async def test_idle_visits_do_not_fill_memory(db, scenario, personas):
    engine = IdleThenActiveEngine("x")
    sim_id = create_simulation(db, scenario, personas, None, engine, days=2, seed=3, baseline=False)
    await Runner(db, engine).run(sim_id)
    notes = [n for r in db.q("SELECT notes_json FROM personas WHERE simulation_id = ?", (sim_id,))
             for n in json.loads(r["notes_json"])]
    assert notes and all(n == "Someone compared prices." for n in notes)


async def test_brand_account_replies_and_stays_out_of_survey(db, scenario, personas):
    from crowdpanel.simulation import load_brand

    engine = get_engine("fake", seed=17)
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=5)
    brand = load_brand(db, sim_id)
    assert brand is not None and brand.name == "Ridgeway Roasters"
    await Runner(db, engine).run(sim_id)
    assert db.one("SELECT author_id FROM posts WHERE kind = 'announcement' AND simulation_id = ?", (sim_id,))[0] == brand.id
    assert db.one("SELECT COUNT(*) AS n FROM calls WHERE simulation_id = ? AND purpose = 'brand'", (sim_id,))["n"] > 0

    await run_survey(db, engine, sim_id)
    surveyed = {r["persona_id"] for r in db.q("SELECT persona_id FROM survey WHERE simulation_id = ?", (sim_id,))}
    assert brand.id not in surveyed and len(surveyed) == len(personas)
    assert aggregate(db, sim_id)["respondents"] == len(personas)


async def test_silent_brand_never_acts(db, scenario, personas):
    from crowdpanel.simulation import load_brand

    scenario.brand_replies = "silent"
    engine = get_engine("fake", seed=17)
    sim_id = create_simulation(db, scenario, personas, None, engine, days=1, seed=5)
    assert load_brand(db, sim_id) is None
    await Runner(db, engine).run(sim_id)
    assert db.one("SELECT COUNT(*) AS n FROM calls WHERE simulation_id = ? AND purpose = 'brand'", (sim_id,))["n"] == 0


def test_brand_prompts_follow_the_mode(scenario):
    from crowdpanel import prompts

    facts = prompts.brand_system(scenario, scenario.offers, 3)
    assert "roast date is printed" in facts and "Never invent" in facts
    scenario.brand_replies = "free"
    assert "Never invent" not in prompts.brand_system(scenario, scenario.offers, 3)
    assert "official account here (@ridgeway_roasters)" in prompts.community_system(scenario, 3)
    scenario.brand_replies = "silent"
    assert "does not answer in this forum" in prompts.community_system(scenario, 3)


def test_brand_mode_is_validated():
    import pytest
    from crowdpanel.loader import ConfigError, parse_scenario

    with pytest.raises(ConfigError):
        parse_scenario({"title": "x", "platform": "y", "context": "z", "offers": [{"description": "d"}],
                        "brand": {"replies": "sometimes"}})


def test_sales_style_instructions_and_facts_file(tmp_path):
    from crowdpanel import prompts
    from crowdpanel.loader import load_scenario

    (tmp_path / "kit.md").write_text("Plans start at $9.90 a month.", encoding="utf-8")
    (tmp_path / "s.yaml").write_text(
        "title: x\nplatform: y\ncontext: z\noffers:\n  - description: d\n"
        "brand:\n  name: Acme Hosting\n  style: sales\n  facts: Founded in 2009.\n  facts_file: kit.md\n"
        "  instructions: Never mention the internal stack.\n", encoding="utf-8")
    s = load_scenario(tmp_path / "s.yaml")
    assert "Founded in 2009." in s.brand_facts and "$9.90" in s.brand_facts
    text = prompts.brand_system(s, s.offers, 3)
    assert "salesperson" in text and "Never mention the internal stack." in text
    s.brand_style = "support"
    assert "salesperson" not in prompts.brand_system(s, s.offers, 3)
