from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from . import prompts
from .db import Database
from .engines import Engine
from .simulation import call_with_retry, load_simulation
from .survey import aggregate, opinion_shifts
from .world import clip


def activity_stats(db: Database, sim_id: int) -> dict[str, Any]:
    posts = db.one("SELECT COUNT(*) AS n FROM posts WHERE simulation_id = ? AND author_id IS NOT NULL", (sim_id,))["n"]
    comments = db.one("SELECT COUNT(*) AS n FROM comments WHERE simulation_id = ?", (sim_id,))["n"]
    likes = db.one("SELECT COUNT(*) AS n FROM likes WHERE simulation_id = ?", (sim_id,))["n"]
    per_persona = db.q(
        "SELECT p.name, p.stance, p.activations, "
        " (SELECT COUNT(*) FROM posts x WHERE x.author_id = p.id) AS posts, "
        " (SELECT COUNT(*) FROM comments c WHERE c.author_id = p.id) AS comments "
        "FROM personas p WHERE p.simulation_id = ? ORDER BY p.name",
        (sim_id,),
    )
    return {
        "posts": posts, "comments": comments, "likes": likes,
        "personas": [dict(r) for r in per_persona],
    }


def top_threads(db: Database, sim_id: int, limit: int = 12) -> list[dict[str, Any]]:
    rows = db.q(
        "SELECT p.*, (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS n_comments, "
        " (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS n_likes "
        "FROM posts p WHERE p.simulation_id = ? ORDER BY (n_comments * 2 + n_likes) DESC, p.id LIMIT ?",
        (sim_id, limit),
    )
    threads = []
    for r in rows:
        comments = db.q("SELECT author_name, content FROM comments WHERE post_id = ? ORDER BY id", (r["id"],))
        threads.append({
            "id": r["id"], "author": r["author_name"], "kind": r["kind"], "content": r["content"],
            "likes": r["n_likes"], "comments": [{"author": c["author_name"], "content": c["content"]} for c in comments],
        })
    return threads


def _material(db: Database, sim_id: int, agg: dict, stats: dict, threads: list[dict],
              before: dict | None = None, shifts: list | None = None) -> str:
    sim, scenario, offers, personas = load_simulation(db, sim_id)
    seg = {p.name: p.segment for p in personas}
    parts = [
        f"SCENARIO: {scenario.title}\nPLACE: {scenario.platform}\nCONTEXT:\n{scenario.context}",
        "OFFER(S) DISCUSSED:\n" + prompts.offers_text(offers),
        f"ACTIVITY: {stats['posts']} posts, {stats['comments']} comments, {stats['likes']} likes "
        f"from {len(personas)} personas.",
        "FINAL OPINION OF EACH PERSONA:\n" + "\n".join(
            f"- {p['name']} ({seg.get(p['name'], '')}): {p['stance'] or '(never formed one)'}" for p in stats["personas"]
        ),
        "SURVEY AFTER THE DISCUSSION (JSON):\n" + json.dumps(agg, ensure_ascii=False),
    ]
    if before and before["respondents"]:
        parts.append("FIRST IMPRESSIONS, EACH PERSONA ALONE BEFORE READING ANYONE (JSON):\n"
                     + json.dumps(before, ensure_ascii=False))
        parts.append("WHO CHANGED THEIR ANSWER DURING THE DISCUSSION (JSON):\n"
                     + json.dumps(shifts or [], ensure_ascii=False))
    thread_text = []
    for t in threads:
        lines = [f"#{t['id']} {t['author']} ({t['likes']} likes): {clip(t['content'], 600)}"]
        lines += [f"  - {c['author']}: {clip(c['content'], 300)}" for c in t["comments"][:12]]
        thread_text.append("\n".join(lines))
    parts.append("MOST DISCUSSED THREADS:\n" + "\n\n".join(thread_text))
    return "\n\n".join(parts)


def _choice_table(q: dict) -> list[str]:
    segments = list(q["by_segment"])
    out = ["| Option | All | " + " | ".join(segments) + " |", "|---|---|" + "---|" * len(segments)]
    for o in q["options"]:
        out.append(f"| {o} | {q['overall'][o]} | " + " | ".join(str(q["by_segment"][s][o]) for s in segments) + " |")
    return out


def stats_markdown(db: Database, sim_id: int, agg: dict, stats: dict, before: dict | None = None,
                   shifts: list | None = None) -> str:
    sim = db.simulation(sim_id)
    spent = db.spend(sim_id)
    out = [
        "## Run details",
        "",
        f"- Engine: {sim['engine']} ({sim['model']})",
        f"- Simulated time: {sim['days']} day(s), {sim['start_hour']:02d}:00 to {sim['end_hour']:02d}:00, "
        f"{sim['current_round']} of {sim['total_rounds']} rounds",
        f"- Activity: {stats['posts']} posts, {stats['comments']} comments, {stats['likes']} likes",
        f"- Model calls: {spent.get('calls', 0)} ({spent.get('errors') or 0} failed), "
        f"{spent.get('input', 0) + spent.get('cached', 0):,} input tokens, {spent.get('output', 0):,} output tokens"
        + (f", about ${spent['cost']:.2f}" if spent.get("cost") else ""),
        "",
        f"## Survey ({agg['respondents']} respondents)",
    ]
    first = {q["id"]: q for q in (before or {}).get("questions", [])} if before and before["respondents"] else {}
    for q in agg["questions"]:
        out += ["", f"### {q['text']}", ""]
        if q["type"] == "choice":
            if q["id"] in first:
                out.append("Before the discussion (each persona alone):")
                out.append("")
                out += _choice_table(first[q["id"]])
                out += ["", "After the discussion:", ""]
            out += _choice_table(q)
        elif q["type"] in ("scale", "number"):
            if q["id"] in first:
                out.append(f"Average: **{first[q['id']]['average']}** before the discussion, **{q['average']}** after")
            else:
                out.append(f"Average: **{q['average']}**")
            if q["by_segment"]:
                out.append("")
                out.append("| Segment | Average |")
                out.append("|---|---|")
                out += [f"| {s} | {v} |" for s, v in q["by_segment"].items()]
        else:
            out += [f"- **{a['name']}** ({a['segment']}): {a['text']}" for a in q["answers"]]
    if first:
        out += ["", "## Who changed their answer", ""]
        if shifts:
            for sh in shifts:
                changes = "; ".join(f"{c['question']} {c['before']} → {c['after']}" for c in sh["changes"])
                out.append(f"- **{sh['name']}** ({sh['segment']}): {changes}")
        else:
            out.append("Nobody changed their choice or score.")
    out += ["", "## Final opinion of each persona", ""]
    out += [f"- **{p['name']}** ({p['posts']} posts, {p['comments']} comments): {p['stance'] or '(none)'}"
            for p in stats["personas"]]
    return "\n".join(out)


async def build_report(db: Database, engine: Engine, sim_id: int, language: str | None = None,
                       narrative: bool = True) -> str:
    sim, scenario, offers, personas = load_simulation(db, sim_id)
    agg = aggregate(db, sim_id)
    before = aggregate(db, sim_id, phase="before")
    shifts = opinion_shifts(db, sim_id)
    stats = activity_stats(db, sim_id)
    threads = top_threads(db, sim_id)
    header = [
        f"# {sim['name']}",
        "",
        f"Simulation {sim_id}, generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by "
        "[crowdpanel](https://github.com/luizsilva-dev/crowdpanel). Personas are simulated: read this as a "
        "map of possible reactions, not as a forecast.",
        "",
    ]
    body = ""
    if narrative:
        res = await call_with_retry(
            db, engine, sim_id, None, None, "report",
            prompts.report_system(language or scenario.language),
            _material(db, sim_id, agg, stats, threads, before, shifts), None, max_tokens=8000,
        )
        body = res.text.strip() + "\n\n"
    content = "\n".join(header) + body + stats_markdown(db, sim_id, agg, stats, before, shifts) + "\n"
    db.run(
        "INSERT INTO reports (simulation_id, content, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT (simulation_id) DO UPDATE SET content = excluded.content, created_at = excluded.created_at",
        (sim_id, content, time.time()),
    )
    db.commit()
    return content


def compare_markdown(db: Database, sim_a: int, sim_b: int) -> str:
    a, b = aggregate(db, sim_a), aggregate(db, sim_b)
    name_a, name_b = db.simulation(sim_a)["name"], db.simulation(sim_b)["name"]
    out = [f"# Comparison", "", f"- A: simulation {sim_a}, {name_a} ({a['respondents']} respondents)",
           f"- B: simulation {sim_b}, {name_b} ({b['respondents']} respondents)", ""]
    qb = {q["id"]: q for q in b["questions"]}
    for qa in a["questions"]:
        q2 = qb.get(qa["id"])
        if not q2 or q2["type"] != qa["type"]:
            continue
        out += [f"## {qa['text']}", ""]
        if qa["type"] == "choice":
            out += ["| Option | A | B |", "|---|---|---|"]
            for o in dict.fromkeys(qa["options"] + q2["options"]):
                out.append(f"| {o} | {qa['overall'].get(o, 0)} | {q2['overall'].get(o, 0)} |")
        elif qa["type"] in ("scale", "number"):
            out.append(f"Average: A **{qa['average']}**, B **{q2['average']}**")
            segs = sorted(set(qa["by_segment"]) | set(q2["by_segment"]))
            if segs:
                out += ["", "| Segment | A | B |", "|---|---|---|"]
                out += [f"| {s} | {qa['by_segment'].get(s, '')} | {q2['by_segment'].get(s, '')} |" for s in segs]
        else:
            continue
        out.append("")
    return "\n".join(out)
