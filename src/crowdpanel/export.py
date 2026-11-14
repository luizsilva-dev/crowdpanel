from __future__ import annotations

import json

from .db import Database


def transcript_markdown(db: Database, sim_id: int) -> str:
    sim = db.simulation(sim_id)
    lines = [f"# {sim['name']}", ""]
    posts = db.q("SELECT * FROM posts WHERE simulation_id = ? ORDER BY id", (sim_id,))
    for p in posts:
        likes = db.one("SELECT COUNT(*) AS n FROM likes WHERE post_id = ?", (p["id"],))["n"]
        label = {"announcement": "announcement", "event": "news", "quote": "quote"}.get(p["kind"], "post")
        lines.append(f"## #{p['id']} {p['author_name']} ({label}, day {p['day']} {p['hour']:02d}:00, {likes} likes)")
        lines.append("")
        if p["quote_of"]:
            lines.append(f"> quoting #{p['quote_of']}")
            lines.append("")
        lines.append(p["content"])
        lines.append("")
        for c in db.q("SELECT * FROM comments WHERE post_id = ? ORDER BY id", (p["id"],)):
            reply = f" (reply to c#{c['reply_to']})" if c["reply_to"] else ""
            lines.append(f"- **{c['author_name']}**{reply}: {c['content']}")
        lines.append("")
    return "\n".join(lines)


def export_json(db: Database, sim_id: int) -> str:
    sim = dict(db.simulation(sim_id))
    sim["scenario"] = json.loads(sim.pop("scenario_json"))
    data = {
        "simulation": sim,
        "personas": [
            {**json.loads(r["data_json"]), "id": r["id"], "final_opinion": r["stance"], "memory": json.loads(r["notes_json"])}
            for r in db.q("SELECT * FROM personas WHERE simulation_id = ? ORDER BY id", (sim_id,))
        ],
        "posts": [dict(r) for r in db.q("SELECT * FROM posts WHERE simulation_id = ? ORDER BY id", (sim_id,))],
        "comments": [dict(r) for r in db.q("SELECT * FROM comments WHERE simulation_id = ? ORDER BY id", (sim_id,))],
        "likes": [dict(r) for r in db.q("SELECT * FROM likes WHERE simulation_id = ?", (sim_id,))],
        "follows": [dict(r) for r in db.q("SELECT * FROM follows WHERE simulation_id = ?", (sim_id,))],
        "activations": [
            {**dict(r), "actions": json.loads(r["actions_json"])}
            for r in db.q("SELECT * FROM activations WHERE simulation_id = ? ORDER BY id", (sim_id,))
        ],
        "survey": [
            {"persona_id": r["persona_id"], "answers": json.loads(r["answers_json"])}
            for r in db.q("SELECT * FROM survey WHERE simulation_id = ?", (sim_id,))
        ],
        "interviews": [dict(r) for r in db.q("SELECT * FROM interviews WHERE simulation_id = ? ORDER BY id", (sim_id,))],
    }
    for a in data["activations"]:
        a.pop("actions_json", None)
    return json.dumps(data, ensure_ascii=False, indent=2)
