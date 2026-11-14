from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import asdict
from typing import Any, Callable

from . import prompts
from .db import Database, dumps
from .engines import Engine, EngineError, Result
from .loader import parse_scenario
from .models import Offer, Persona, Scenario
from .world import World, actions_json

Progress = Callable[[str], None]

# rough sizes used only for the estimate shown before a run
EST_INPUT_PER_ACTIVATION = 3200
EST_OUTPUT_PER_ACTIVATION = 350
MEMORY_NOTES = 8


class SimulationStopped(Exception):
    pass


def scenario_to_dict(s: Scenario) -> dict[str, Any]:
    return {
        "title": s.title, "platform": s.platform, "context": s.context, "language": s.language,
        "brand_name": s.brand_name, "announcement": s.announcement,
        "brand": {"name": s.brand_name, "replies": s.brand_replies, "facts": s.brand_facts,
                  "activity": s.brand_activity, "active_hours": s.brand_hours},
        "offers": [asdict(o) for o in s.offers],
        "questions": [asdict(q) for q in s.questions],
        "events": [asdict(e) for e in s.events],
    }


def round_clock(round_no: int, start_hour: int, end_hour: int) -> tuple[int, int]:
    per_day = end_hour - start_hour + 1
    return round_no // per_day + 1, start_hour + round_no % per_day


def create_simulation(db: Database, scenario: Scenario, personas: list[Persona], offer_ids: list[str] | None,
                      engine: Engine, name: str = "", days: int = 3, start_hour: int = 8, end_hour: int = 22,
                      seed: int | None = None, parallel: int | None = None, feed_size: int = 5,
                      max_actions: int = 3, max_calls: int | None = None, max_cost: float | None = None,
                      engine_options: dict | None = None, baseline: bool = True) -> int:
    if not 0 <= start_hour <= end_hour <= 23:
        raise ValueError("hours must satisfy 0 <= start_hour <= end_hour <= 23")
    if days < 1:
        raise ValueError("days must be at least 1")
    offers = [scenario.offer(o) for o in offer_ids] if offer_ids else scenario.offers
    seed = seed if seed is not None else random.randint(1, 10**6)
    total_rounds = days * (end_hour - start_hour + 1)
    sim_id = db.run(
        "INSERT INTO simulations (name, created_at, scenario_json, offer_ids, engine, model, engine_options, days, "
        "start_hour, end_hour, seed, parallel, feed_size, max_actions, max_calls, max_cost, total_rounds, baseline) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name or f"{scenario.title} ({', '.join(o.name for o in offers)})", time.time(),
            dumps(scenario_to_dict(scenario)), dumps([o.id for o in offers]), engine.name, engine.model,
            dumps(engine_options or {}), days, start_hour, end_hour, seed, parallel or engine.default_parallel,
            feed_size, max_actions, max_calls, max_cost, total_rounds, int(baseline),
        ),
    )
    for p in personas:
        p.id = db.run(
            "INSERT INTO personas (simulation_id, name, data_json) VALUES (?, ?, ?)",
            (sim_id, p.name, dumps(p.to_dict())),
        )
    if scenario.brand_replies != "silent":
        brand = brand_persona(scenario)
        db.run(
            "INSERT INTO personas (simulation_id, name, data_json, role) VALUES (?, ?, ?, 'brand')",
            (sim_id, brand.name, dumps(brand.to_dict())),
        )
    db.commit()
    return sim_id


def brand_persona(scenario: Scenario) -> Persona:
    return Persona(name=scenario.brand_name, profile=f"Official account of {scenario.brand_name}.", segment="company",
                   activity=scenario.brand_activity, active_hours=scenario.brand_hours)


def _persona_from_row(pr: Any) -> Persona:
    p = Persona(**json.loads(pr["data_json"]))
    p.id = pr["id"]
    return p


def load_brand(db: Database, sim_id: int) -> Persona | None:
    row = db.one("SELECT * FROM personas WHERE simulation_id = ? AND role = 'brand'", (sim_id,))
    return _persona_from_row(row) if row else None


def load_simulation(db: Database, sim_id: int) -> tuple[Any, Scenario, list[Offer], list[Persona]]:
    row = db.simulation(sim_id)
    scenario = parse_scenario(json.loads(row["scenario_json"]))
    offers = [scenario.offer(o) for o in json.loads(row["offer_ids"])]
    personas = [_persona_from_row(pr) for pr in db.q(
        "SELECT * FROM personas WHERE simulation_id = ? AND role = 'customer' ORDER BY id", (sim_id,))]
    return row, scenario, offers, personas


def estimate(personas: list[Persona], days: int, start_hour: int, end_hour: int,
             baseline: bool = True, scenario: Scenario | None = None) -> dict[str, float]:
    hours = range(start_hour, end_hour + 1)
    crowd = list(personas)
    if scenario is not None and scenario.brand_replies != "silent":
        crowd.append(brand_persona(scenario))
    per_day = sum(p.activity for p in crowd for h in hours if h in p.active_hours)
    activations = per_day * days
    surveys = len(personas) * (2 if baseline else 1)
    calls = activations + surveys + 1
    return {
        "activations": round(activations),
        "calls": round(calls),
        "input_tokens": round(activations * EST_INPUT_PER_ACTIVATION + surveys * 2500 + 30000),
        "output_tokens": round(activations * EST_OUTPUT_PER_ACTIVATION + surveys * 400 + 3000),
    }


async def call_with_retry(db: Database, engine: Engine, sim_id: int | None, round_no: int | None,
                          persona_id: int | None, purpose: str, system: str, prompt: str,
                          schema: dict | None, max_tokens: int = 4000, tries: int = 3) -> Result:
    last: Exception | None = None
    for attempt in range(tries):
        started = time.monotonic()
        try:
            result = await engine.generate(system, prompt, schema, max_tokens=max_tokens)
            db.log_call(sim_id, round_no, persona_id, purpose, engine.name, engine.model, result.usage, result.seconds)
            db.commit()
            return result
        except EngineError as e:
            db.log_call(sim_id, round_no, persona_id, purpose, engine.name, engine.model,
                        seconds=time.monotonic() - started, error=str(e)[:500])
            db.commit()
            last = e
            if not e.retryable:
                raise
            await asyncio.sleep(1.5 * (attempt + 1) ** 2)
    assert last is not None
    raise last


class Runner:
    def __init__(self, db: Database, engine: Engine, progress: Progress | None = None):
        self.db = db
        self.engine = engine
        self.progress = progress or (lambda msg: None)

    def _memory(self, persona: Persona) -> tuple[str, list[str]]:
        row = self.db.one("SELECT stance, notes_json FROM personas WHERE id = ?", (persona.id,))
        return row["stance"], json.loads(row["notes_json"])

    def _remember(self, persona: Persona, opinion: str, note: str) -> None:
        stance, notes = self._memory(persona)
        if note:
            notes = (notes + [note.strip()])[-MEMORY_NOTES:]
        self.db.run(
            "UPDATE personas SET stance = ?, notes_json = ?, activations = activations + 1 WHERE id = ?",
            ((opinion or stance).strip(), dumps(notes), persona.id),
        )

    def _seed(self, world: World, scenario: Scenario, offers: list[Offer], start_hour: int,
              brand: Persona | None = None) -> None:
        if self.db.one("SELECT 1 FROM posts WHERE simulation_id = ? AND kind = 'announcement'", (world.sim_id,)):
            return
        text = scenario.announcement or f"{scenario.brand_name} presents:"
        body = text + "\n\n" + prompts.offers_text(offers)
        world.add_post(0, 1, start_hour, brand, scenario.brand_name, body, kind="announcement")

    def _budget_reached(self, sim: Any) -> str | None:
        spent = self.db.spend(sim["id"])
        if sim["max_calls"] and spent.get("calls", 0) >= sim["max_calls"]:
            return f"call limit reached ({spent['calls']} of {sim['max_calls']})"
        if sim["max_cost"] and (spent.get("cost") or 0) >= sim["max_cost"]:
            return f"cost limit reached (${spent['cost']:.2f} of ${sim['max_cost']:.2f})"
        return None

    async def run(self, sim_id: int) -> str:
        sim, scenario, offers, personas = load_simulation(self.db, sim_id)
        if sim["status"] == "done":
            self.progress("Simulation already finished.")
            return "done"
        brand = load_brand(self.db, sim_id)
        world = World(self.db, sim_id, personas + ([brand] if brand else []))
        system = prompts.community_system(scenario, sim["max_actions"])
        brand_system = prompts.brand_system(scenario, offers, sim["max_actions"])
        semaphore = asyncio.Semaphore(sim["parallel"])
        self.db.set_status(sim_id, "running")
        self._seed(world, scenario, offers, sim["start_hour"], brand)
        self.db.commit()

        if sim["baseline"] and sim["current_round"] == 0:
            from .survey import run_survey

            answered = self.db.one("SELECT COUNT(*) AS n FROM survey WHERE simulation_id = ? AND phase = 'before'",
                                   (sim_id,))["n"]
            if answered < len(personas):
                self.progress("Asking each persona for a first impression, alone, before the discussion...")
                try:
                    await run_survey(self.db, self.engine, sim_id, progress=self.progress, phase="before")
                except EngineError as e:
                    self.db.set_status(sim_id, "failed", str(e))
                    self.progress(f"Failed: {e}")
                    return "failed"

        async def activate(p: Persona, round_no: int, day: int, hour: int, feed: str) -> tuple[Persona, Any]:
            opinion, notes = self._memory(p)
            if brand is not None and p.id == brand.id:
                purpose, sys_prompt = "brand", brand_system
                prompt = prompts.brand_prompt(scenario, opinion, notes, world.own_writing(p, 5), day, hour, feed)
            else:
                purpose, sys_prompt = "activation", system
                prompt = prompts.activation_prompt(p, opinion, notes, world.own_writing(p), day, hour, feed)
            async with semaphore:
                try:
                    res = await call_with_retry(self.db, self.engine, sim_id, round_no, p.id, purpose,
                                                sys_prompt, prompt, prompts.ACTION_SCHEMA, max_tokens=3000)
                    return p, res.data
                except EngineError as e:
                    return p, e

        try:
            for round_no in range(sim["current_round"], sim["total_rounds"]):
                current = self.db.simulation(sim_id)
                if current["status"] == "stopping":
                    raise SimulationStopped("stopped by user")
                reason = self._budget_reached(current)
                if reason:
                    self.db.set_status(sim_id, "paused", reason)
                    self.progress(f"Paused: {reason}. Raise the limit and resume to continue.")
                    return "paused"

                day, hour = round_clock(round_no, sim["start_hour"], sim["end_hour"])
                for ev in scenario.events:
                    if ev.day == day and ev.hour == hour:
                        world.add_post(round_no, day, hour, None, ev.author or scenario.brand_name, ev.content, kind="event")

                rng = random.Random(f"{sim['seed']}-{round_no}")
                awake = [p for p in personas if hour in p.active_hours and rng.random() < p.activity]
                if brand is not None and hour in brand.active_hours and rng.random() < brand.activity:
                    awake.append(brand)
                if not awake:
                    self.db.run("UPDATE simulations SET current_round = ? WHERE id = ?", (round_no + 1, sim_id))
                    self.db.commit()
                    continue

                feeds = {}
                for p in awake:
                    feed, shown = world.build_feed(p, round_no, sim["feed_size"], rng)
                    feeds[p.id] = feed
                    world.mark_seen(p, shown)

                results = await asyncio.gather(*(activate(p, round_no, day, hour, feeds[p.id]) for p in awake))

                fatal = [r for _, r in results if isinstance(r, EngineError) and not r.retryable]
                if fatal:
                    raise fatal[0]
                failed = sum(1 for _, r in results if isinstance(r, Exception))
                if failed and failed * 2 > len(results):
                    raise EngineError(f"{failed} of {len(results)} persona calls failed in this round; "
                                      f"last error: {next(r for _, r in results if isinstance(r, Exception))}")

                done_count = 0
                rng.shuffle(results)
                for p, data in results:
                    if not isinstance(data, dict):
                        continue
                    done = world.apply(p, data.get("actions") or [], round_no, day, hour, sim["max_actions"])
                    done_count += sum(1 for d in done if d["type"] != "nothing")
                    self.db.run(
                        "INSERT INTO activations (simulation_id, round, persona_id, thought, stance, note, actions_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (sim_id, round_no, p.id, data.get("thought", ""), data.get("opinion", ""),
                         data.get("memory_note", ""), actions_json(done)),
                    )
                    # an idle visit leaves no note, so "nothing new" lines don't push real memories out
                    acted = any(d["type"] != "nothing" for d in done)
                    self._remember(p, data.get("opinion", ""), data.get("memory_note", "") if acted else "")
                self.db.run("UPDATE simulations SET current_round = ? WHERE id = ?", (round_no + 1, sim_id))
                self.db.commit()
                note = f", {failed} failed" if failed else ""
                self.progress(f"Day {day} {hour:02d}:00  {len(awake)} awake, {done_count} actions{note}  "
                              f"[{round_no + 1}/{sim['total_rounds']}]")
        except SimulationStopped as e:
            self.db.commit()
            self.db.set_status(sim_id, "paused", str(e))
            self.progress("Stopped. Resume any time.")
            return "paused"
        except (asyncio.CancelledError, KeyboardInterrupt):
            self.db.conn.rollback()
            self.db.set_status(sim_id, "paused", "interrupted")
            raise
        except EngineError as e:
            self.db.conn.rollback()
            self.db.set_status(sim_id, "failed", str(e))
            self.progress(f"Failed: {e}")
            return "failed"

        self.db.set_status(sim_id, "done")
        self.progress("Simulation finished.")
        return "done"
