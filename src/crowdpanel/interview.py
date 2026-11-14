from __future__ import annotations

import asyncio
import json
import time

from . import prompts
from .db import Database
from .engines import Engine, EngineError
from .models import Persona
from .simulation import call_with_retry, load_simulation
from .world import World


def find_persona(personas: list[Persona], name: str) -> Persona:
    key = name.lower().lstrip("@").strip()
    for p in personas:
        if p.name.lower() == key or p.handle().lstrip("@") == key:
            return p
    matches = [p for p in personas if key in p.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise LookupError(f"no single persona matches '{name}'")


async def ask(db: Database, engine: Engine, sim_id: int, question: str,
              names: list[str] | None = None) -> list[tuple[Persona, str]]:
    sim, scenario, offers, personas = load_simulation(db, sim_id)
    targets = [find_persona(personas, n) for n in names] if names else personas
    world = World(db, sim_id, personas)
    system = prompts.survey_system(scenario)
    semaphore = asyncio.Semaphore(sim["parallel"])

    async def one(p: Persona):
        row = db.one("SELECT stance, notes_json FROM personas WHERE id = ?", (p.id,))
        prompt = prompts.interview_prompt(p, row["stance"], json.loads(row["notes_json"]),
                                          world.own_writing(p, 5), offers, question)
        async with semaphore:
            try:
                res = await call_with_retry(db, engine, sim_id, None, p.id, "interview", system, prompt, None, 1500)
                return p, res.text.strip()
            except EngineError as e:
                return p, e

    answers = []
    for p, answer in await asyncio.gather(*(one(p) for p in targets)):
        if isinstance(answer, EngineError):
            if not answer.retryable:
                raise answer
            answers.append((p, f"(no answer: {answer})"))
            continue
        db.run(
            "INSERT INTO interviews (simulation_id, persona_id, question, answer, created_at) VALUES (?, ?, ?, ?, ?)",
            (sim_id, p.id, question, answer, time.time()),
        )
        answers.append((p, answer))
    db.commit()
    return answers
