from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import Event, Offer, Persona, Question, Scenario


class ConfigError(ValueError):
    pass


def _read_yaml(path: str | Path) -> Any:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"file not found: {p}")
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def default_questions(offers: list[Offer]) -> list[Question]:
    names = [o.name for o in offers]
    qs = [
        Question("purchase_intent", "From 0 to 10, how likely are you to buy or sign up?", "scale", min=0, max=10),
        Question("willingness_to_pay", "What is the most you would pay for this, and how would you want to pay it?"),
        Question("main_objection", "What is the main thing holding you back, if anything?"),
        Question("what_would_change_your_mind", "What would make you change your mind?"),
        Question("best_part", "What did you like the most?"),
    ]
    if len(names) > 1:
        qs.insert(0, Question("preferred_offer", "Which option would you pick?", "choice", options=names + ["None of them"]))
    else:
        qs.insert(0, Question("would_buy", "Would you buy it?", "choice", options=["Yes", "Maybe", "No"]))
    return qs


def parse_hours(value: Any, default: list[int]) -> list[int]:
    if not value:
        return default
    if isinstance(value, str) and "-" in value:
        start, end = (int(x) for x in value.split("-", 1))
        return list(range(start, end + 1))
    return [int(h) for h in value]


def parse_scenario(data: dict[str, Any]) -> Scenario:
    if not isinstance(data, dict):
        raise ConfigError("scenario must be a YAML mapping")
    for key in ("title", "platform", "context", "offers"):
        if not data.get(key):
            raise ConfigError(f"scenario is missing '{key}'")

    offers = []
    for i, raw in enumerate(data["offers"]):
        if not raw.get("description"):
            raise ConfigError(f"offer #{i + 1} has no description")
        offers.append(Offer(
            id=str(raw.get("id") or chr(ord("a") + i)),
            name=str(raw.get("name") or f"Option {chr(ord('A') + i)}"),
            description=str(raw["description"]).strip(),
        ))
    if len({o.id for o in offers}) != len(offers):
        raise ConfigError("offer ids must be unique")

    questions = []
    for raw in data.get("questions") or []:
        q = Question(
            id=str(raw["id"]),
            text=str(raw["text"]),
            type=str(raw.get("type", "text")),
            options=[str(x) for x in raw.get("options", [])],
            min=int(raw.get("min", 0)),
            max=int(raw.get("max", 10)),
        )
        if q.type not in ("text", "choice", "scale", "number"):
            raise ConfigError(f"question '{q.id}': unknown type '{q.type}'")
        if q.type == "choice" and not q.options:
            raise ConfigError(f"question '{q.id}': choice questions need options")
        questions.append(q)

    events = []
    for raw in data.get("events") or []:
        events.append(Event(
            day=int(raw["day"]),
            hour=int(raw.get("hour", 10)),
            content=str(raw["content"]).strip(),
            author=str(raw.get("author", "")),
        ))

    brand = data.get("brand") or {}
    if not isinstance(brand, dict):
        raise ConfigError("'brand' must be a mapping")
    replies = str(brand.get("replies", "facts"))
    if replies not in ("facts", "free", "silent"):
        raise ConfigError("brand.replies must be facts, free or silent")
    brand_activity = float(brand.get("activity", 0.7))
    if not 0 <= brand_activity <= 1:
        raise ConfigError("brand.activity must be between 0 and 1")

    return Scenario(
        title=str(data["title"]),
        platform=str(data["platform"]).strip(),
        context=str(data["context"]).strip(),
        offers=offers,
        language=str(data.get("language", "English")),
        brand_name=str(brand.get("name") or data.get("brand_name") or "the company"),
        brand_replies=replies,
        brand_facts=str(brand.get("facts", "")).strip(),
        brand_activity=brand_activity,
        brand_hours=parse_hours(brand.get("active_hours"), list(range(9, 19))),
        announcement=str(data.get("announcement", "")).strip(),
        questions=questions,
        events=events,
    )


def load_scenario(path: str | Path) -> Scenario:
    return parse_scenario(_read_yaml(path))


def parse_personas(data: Any) -> list[Persona]:
    if isinstance(data, dict):
        data = data.get("personas")
    if not isinstance(data, list) or not data:
        raise ConfigError("personas file must contain a non-empty list (or a 'personas:' key with one)")
    personas = []
    seen = set()
    for i, raw in enumerate(data):
        name = str(raw.get("name", "")).strip()
        profile = str(raw.get("profile") or raw.get("background") or "").strip()
        if not name or not profile:
            raise ConfigError(f"persona #{i + 1} needs at least 'name' and 'profile'")
        if name.lower() in seen:
            raise ConfigError(f"duplicated persona name: {name}")
        seen.add(name.lower())
        hours = parse_hours(raw.get("active_hours"), list(range(8, 23)))
        activity = float(raw.get("activity", 0.5))
        if not 0 <= activity <= 1:
            raise ConfigError(f"persona {name}: activity must be between 0 and 1")
        personas.append(Persona(
            name=name,
            profile=profile,
            segment=str(raw.get("segment", "general")),
            age=int(raw["age"]) if raw.get("age") else None,
            location=str(raw.get("location", "")),
            occupation=str(raw.get("occupation", "")),
            personality=str(raw.get("personality", "")),
            activity=activity,
            active_hours=[int(h) for h in hours],
        ))
    return personas


def load_personas(path: str | Path) -> list[Persona]:
    return parse_personas(_read_yaml(path))


def dump_personas(personas: list[Persona], path: str | Path) -> None:
    with Path(path).open("w", encoding="utf-8") as fh:
        yaml.safe_dump({"personas": [p.to_dict() for p in personas]}, fh, allow_unicode=True, sort_keys=False, width=100)
