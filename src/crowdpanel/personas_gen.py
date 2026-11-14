from __future__ import annotations

from . import prompts
from .db import Database
from .engines import Engine
from .models import Persona, Scenario
from .simulation import Progress, call_with_retry

BATCH = 6


async def generate_personas(db: Database, engine: Engine, scenario: Scenario, count: int, spec: str = "",
                            progress: Progress | None = None) -> list[Persona]:
    progress = progress or (lambda m: None)
    personas: list[Persona] = []
    system = prompts.persona_generation_system()
    empty_batches = 0
    while len(personas) < count and empty_batches < 3:
        n = min(BATCH, count - len(personas))
        prompt = prompts.persona_generation_prompt(scenario, spec, n, [p.name for p in personas])
        res = await call_with_retry(db, engine, None, None, None, "personas", system, prompt,
                                    prompts.PERSONA_SCHEMA, max_tokens=8000)
        taken = {p.name.lower() for p in personas}
        before = len(personas)
        for raw in (res.data or {}).get("personas", [])[:n]:
            name = str(raw.get("name", "")).strip()
            if not name or name.lower() in taken:
                continue
            taken.add(name.lower())
            try:
                activity = float(raw.get("activity", 0.5))
            except (TypeError, ValueError):
                activity = 0.5
            personas.append(Persona(
                name=name,
                profile=str(raw.get("profile", "")).strip(),
                segment=str(raw.get("segment", "general")).strip() or "general",
                age=int(raw["age"]) if str(raw.get("age", "")).isdigit() else None,
                location=str(raw.get("location", "")),
                occupation=str(raw.get("occupation", "")),
                personality=str(raw.get("personality", "")),
                activity=max(0.1, min(0.9, activity)),
            ))
        empty_batches = empty_batches + 1 if len(personas) == before else 0
        progress(f"{len(personas)} of {count} personas")
    db.commit()
    return personas[:count]
