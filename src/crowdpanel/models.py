from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Offer:
    id: str
    name: str
    description: str


@dataclass
class Question:
    id: str
    text: str
    type: str = "text"  # text | choice | scale | number
    options: list[str] = field(default_factory=list)
    min: int = 0
    max: int = 10


@dataclass
class Event:
    day: int
    hour: int
    content: str
    author: str = ""  # empty means the brand account


@dataclass
class Persona:
    name: str
    profile: str
    segment: str = "general"
    age: int | None = None
    location: str = ""
    occupation: str = ""
    personality: str = ""
    activity: float = 0.5
    active_hours: list[int] = field(default_factory=lambda: list(range(8, 23)))
    id: int | None = None

    def handle(self) -> str:
        return "@" + "".join(ch for ch in self.name.lower().replace(" ", "_") if ch.isalnum() or ch == "_")

    def card(self) -> str:
        parts = [f"Name: {self.name}"]
        if self.age:
            parts.append(f"Age: {self.age}")
        if self.location:
            parts.append(f"Location: {self.location}")
        if self.occupation:
            parts.append(f"Occupation: {self.occupation}")
        if self.personality:
            parts.append(f"Personality: {self.personality}")
        parts.append(f"Background: {self.profile.strip()}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "profile": self.profile,
            "segment": self.segment,
            "age": self.age,
            "location": self.location,
            "occupation": self.occupation,
            "personality": self.personality,
            "activity": self.activity,
            "active_hours": self.active_hours,
        }


@dataclass
class Scenario:
    title: str
    platform: str
    context: str
    offers: list[Offer]
    language: str = "English"
    brand_name: str = "the company"
    # how the company account behaves in the forum: facts (answers only from brand_facts and the
    # offers), free (improvises like a founder; the report lists what it promised) or silent
    brand_replies: str = "facts"
    brand_facts: str = ""
    brand_activity: float = 0.7
    brand_hours: list[int] = field(default_factory=lambda: list(range(9, 19)))
    brand_style: str = "support"  # support (answers questions) or sales (also argues and handles objections)
    brand_instructions: str = ""
    announcement: str = ""
    questions: list[Question] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    def offer(self, offer_id: str) -> Offer:
        for o in self.offers:
            if o.id == offer_id:
                return o
        raise KeyError(f"offer '{offer_id}' not found in scenario (have: {', '.join(o.id for o in self.offers)})")
