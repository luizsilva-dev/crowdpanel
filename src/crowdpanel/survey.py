from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from typing import Any

from . import prompts
from .db import Database, dumps
from .engines import Engine, EngineError
from .loader import default_questions
from .models import Offer, Persona, Question, Scenario
from .simulation import Progress, call_with_retry, load_simulation
from .world import World


def questions_for(scenario: Scenario, offers: list[Offer]) -> list[Question]:
    return scenario.questions or default_questions(offers)


def _validate(answer: dict, questions: list[Question]) -> dict:
    out = {}
    for q in questions:
        value = answer.get(q.id)
        if q.type == "scale":
            try:
                value = max(q.min, min(q.max, int(round(float(value)))))
            except (TypeError, ValueError):
                value = None
        elif q.type == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        elif q.type == "choice" and value not in q.options:
            value = None
        out[q.id] = value
    return out


async def run_survey(db: Database, engine: Engine, sim_id: int, redo: bool = False,
                     progress: Progress | None = None, phase: str = "after") -> int:
    """phase "before" asks every persona alone, before the discussion, so the report can show
    how much the conversation moved people; "after" asks with everything they lived through."""
    progress = progress or (lambda m: None)
    sim, scenario, offers, personas = load_simulation(db, sim_id)
    questions = questions_for(scenario, offers)
    world = World(db, sim_id, personas)
    system = prompts.survey_system(scenario)
    schema = prompts.survey_schema(questions)
    done_ids = {r["persona_id"] for r in db.q(
        "SELECT persona_id FROM survey WHERE simulation_id = ? AND phase = ?", (sim_id, phase))}
    first_look = phase == "before"
    todo = [p for p in personas if redo or p.id not in done_ids]
    semaphore = asyncio.Semaphore(sim["parallel"])

    async def ask(p: Persona):
        if first_look:
            prompt = prompts.survey_prompt(p, "", [], [], offers, questions, first_look=True)
        else:
            row = db.one("SELECT stance, notes_json FROM personas WHERE id = ?", (p.id,))
            prompt = prompts.survey_prompt(p, row["stance"], json.loads(row["notes_json"]), world.own_writing(p, 5),
                                           offers, questions)
        async with semaphore:
            try:
                res = await call_with_retry(db, engine, sim_id, None, p.id, f"survey-{phase}", system, prompt,
                                            schema, 2000)
                return p, res.data
            except EngineError as e:
                return p, e

    results = await asyncio.gather(*(ask(p) for p in todo))
    saved = 0
    for p, data in results:
        if isinstance(data, EngineError):
            progress(f"Survey failed for {p.name}: {data}")
            if not data.retryable:
                raise data
            continue
        db.run(
            "INSERT INTO survey (simulation_id, persona_id, phase, answers_json, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (simulation_id, persona_id, phase) DO UPDATE SET answers_json = excluded.answers_json, "
            "created_at = excluded.created_at",
            (sim_id, p.id, phase, dumps(_validate(data or {}, questions)), time.time()),
        )
        saved += 1
    db.commit()
    label = "First impressions" if first_look else "Survey"
    progress(f"{label}: {saved} of {len(todo)} personas answered.")
    return saved


def aggregate(db: Database, sim_id: int, phase: str = "after") -> dict[str, Any]:
    sim, scenario, offers, personas = load_simulation(db, sim_id)
    questions = questions_for(scenario, offers)
    by_id = {p.id: p for p in personas}
    rows = db.q("SELECT persona_id, answers_json FROM survey WHERE simulation_id = ? AND phase = ?", (sim_id, phase))
    answers = [(by_id[r["persona_id"]], json.loads(r["answers_json"])) for r in rows if r["persona_id"] in by_id]

    result: dict[str, Any] = {"respondents": len(answers), "questions": []}
    for q in questions:
        entry: dict[str, Any] = {"id": q.id, "text": q.text, "type": q.type}
        if q.type == "choice":
            overall = Counter(a.get(q.id) for _, a in answers if a.get(q.id))
            seg: dict[str, Counter] = defaultdict(Counter)
            for p, a in answers:
                if a.get(q.id):
                    seg[p.segment][a[q.id]] += 1
            entry["options"] = q.options
            entry["overall"] = {o: overall.get(o, 0) for o in q.options}
            entry["by_segment"] = {s: {o: c.get(o, 0) for o in q.options} for s, c in sorted(seg.items())}
        elif q.type in ("scale", "number"):
            vals = [a.get(q.id) for _, a in answers if isinstance(a.get(q.id), (int, float))]
            entry["average"] = round(sum(vals) / len(vals), 1) if vals else None
            seg_vals: dict[str, list] = defaultdict(list)
            for p, a in answers:
                if isinstance(a.get(q.id), (int, float)):
                    seg_vals[p.segment].append(a[q.id])
            entry["by_segment"] = {s: round(sum(v) / len(v), 1) for s, v in sorted(seg_vals.items())}
            if q.type == "scale":
                entry["distribution"] = {str(i): sum(1 for v in vals if v == i) for i in range(q.min, q.max + 1)}
        else:
            entry["answers"] = [
                {"name": p.name, "segment": p.segment, "text": a.get(q.id)}
                for p, a in sorted(answers, key=lambda x: (x[0].segment, x[0].name)) if a.get(q.id)
            ]
        result["questions"].append(entry)
    return result


def opinion_shifts(db: Database, sim_id: int) -> list[dict[str, Any]]:
    """Personas whose choice or score changed between the first impression and the end."""
    sim, scenario, offers, personas = load_simulation(db, sim_id)
    questions = [q for q in questions_for(scenario, offers) if q.type in ("choice", "scale")]
    by_id = {p.id: p for p in personas}
    answers: dict[int, dict[str, dict]] = {}
    for r in db.q("SELECT persona_id, phase, answers_json FROM survey WHERE simulation_id = ?", (sim_id,)):
        answers.setdefault(r["persona_id"], {})[r["phase"]] = json.loads(r["answers_json"])
    shifts = []
    for pid, phases in answers.items():
        if "before" not in phases or "after" not in phases or pid not in by_id:
            continue
        changes = []
        for q in questions:
            a, b = phases["before"].get(q.id), phases["after"].get(q.id)
            if a is not None and b is not None and a != b:
                changes.append({"question": q.text, "before": a, "after": b})
        if changes:
            p = by_id[pid]
            shifts.append({"name": p.name, "segment": p.segment, "changes": changes})
    return sorted(shifts, key=lambda x: (x["segment"], x["name"]))
