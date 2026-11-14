from __future__ import annotations

import json
import math
import random
from typing import Any

from .db import Database
from .models import Persona

MAX_TEXT = 1500


def clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 3] + "..."


class World:
    """The forum: posts, comments, likes, follows, and each persona's feed."""

    def __init__(self, db: Database, sim_id: int, personas: list[Persona]):
        self.db = db
        self.sim_id = sim_id
        self.personas = {p.id: p for p in personas}
        self.by_handle = {p.handle().lower(): p for p in personas}

    def add_post(self, round_no: int, day: int, hour: int, author: Persona | None, author_name: str,
                 content: str, kind: str = "post", quote_of: int | None = None) -> int:
        return self.db.run(
            "INSERT INTO posts (simulation_id, round, day, hour, author_id, author_name, kind, content, quote_of) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.sim_id, round_no, day, hour, author.id if author else None,
             author.name if author else author_name, kind, content[:MAX_TEXT], quote_of),
        )

    def _handle_of(self, author_id: int | None, author_name: str) -> str:
        p = self.personas.get(author_id) if author_id is not None else None
        return p.handle() if p else author_name

    def post_exists(self, post_id: int | None) -> bool:
        if post_id is None:
            return False
        return self.db.one("SELECT 1 FROM posts WHERE id = ? AND simulation_id = ?", (post_id, self.sim_id)) is not None

    def comment_on_post(self, comment_id: int | None, post_id: int) -> bool:
        if comment_id is None:
            return False
        return self.db.one("SELECT 1 FROM comments WHERE id = ? AND post_id = ?", (comment_id, post_id)) is not None

    def build_feed(self, persona: Persona, round_no: int, feed_size: int, rng: random.Random) -> tuple[str, list[int]]:
        pinned = self.db.q(
            "SELECT p.*, (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count "
            "FROM posts p WHERE p.simulation_id = ? AND p.kind = 'announcement' ORDER BY p.id", (self.sim_id,)
        )
        others = self.db.q(
            "SELECT p.*, "
            " (SELECT COUNT(*) FROM likes l WHERE l.post_id = p.id) AS like_count, "
            " (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comment_count, "
            " s.comments_seen AS comments_seen, s.post_id AS seen_post, "
            " (SELECT 1 FROM follows f WHERE f.simulation_id = p.simulation_id AND f.follower_id = ? "
            "   AND f.followee_id = p.author_id) AS followed "
            "FROM posts p LEFT JOIN seen s ON s.simulation_id = p.simulation_id AND s.persona_id = ? AND s.post_id = p.id "
            "WHERE p.simulation_id = ? AND p.kind != 'announcement' AND (p.author_id IS NULL OR p.author_id != ?)",
            (persona.id, persona.id, self.sim_id, persona.id),
        )

        scored = []
        for row in others:
            age = max(0, round_no - row["round"])
            score = 1.0 / (1.0 + age / 6.0)
            if row["seen_post"] is None:
                score += 3.0
            elif row["comment_count"] > (row["comments_seen"] or 0):
                score += 2.0
            else:
                score -= 2.0
            if row["followed"]:
                score += 2.0
            if row["kind"] == "event":
                score += 2.5
            score += math.log1p(row["like_count"] + row["comment_count"]) * 0.8
            score += rng.random() * 0.8
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [row for score, row in scored[:feed_size] if score > 0]

        blocks = []
        shown = []
        for row in pinned:
            # the offer stays in full on every visit, with the latest replies under it
            blocks.append("PINNED " + self._render_post(row, full=True, comments=6))
            shown.append(row["id"])
        for row in picked:
            blocks.append(self._render_post(row))
            shown.append(row["id"])
        if not picked:
            blocks.append("(Nothing new from other members since your last visit.)")
        return "\n\n".join(blocks), shown

    def _render_post(self, row: Any, full: bool = False, comments: int = 4) -> str:
        who = self._handle_of(row["author_id"], row["author_name"])
        head = f"#{row['id']} {who} (day {row['day']}, {row['hour']:02d}:00"
        likes = row["like_count"] if "like_count" in row.keys() else 0
        head += f", {likes} likes)"
        if row["kind"] == "event":
            head = f"#{row['id']} NEWS from {row['author_name']} (day {row['day']}, {row['hour']:02d}:00)"
        text = f"{head}:\n{row['content'] if full else clip(row['content'], 900)}"
        if row["quote_of"]:
            q = self.db.one("SELECT * FROM posts WHERE id = ?", (row["quote_of"],))
            if q:
                text += f"\n  [quoting #{q['id']} {self._handle_of(q['author_id'], q['author_name'])}: {clip(q['content'], 200)}]"
        recent = self.db.q(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY id DESC LIMIT ?", (row["id"], comments)
        )
        total = self.db.one("SELECT COUNT(*) AS n FROM comments WHERE post_id = ?", (row["id"],))["n"]
        if total > len(recent):
            text += f"\n  ({total - len(recent)} older comments hidden)"
        for c in reversed(recent):
            reply = f" replying to c#{c['reply_to']}" if c["reply_to"] else ""
            text += f"\n  c#{c['id']} {self._handle_of(c['author_id'], c['author_name'])}{reply}: {clip(c['content'], 300)}"
        return text

    def mark_seen(self, persona: Persona, post_ids: list[int]) -> None:
        for pid in post_ids:
            n = self.db.one("SELECT COUNT(*) AS n FROM comments WHERE post_id = ?", (pid,))["n"]
            self.db.run(
                "INSERT INTO seen (simulation_id, persona_id, post_id, comments_seen) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (simulation_id, persona_id, post_id) DO UPDATE SET comments_seen = excluded.comments_seen",
                (self.sim_id, persona.id, pid, n),
            )

    def apply(self, persona: Persona, actions: list[dict], round_no: int, day: int, hour: int,
              max_actions: int) -> list[dict]:
        """Applies the actions that make sense and returns what was actually done."""
        done = []
        for act in actions[:max_actions]:
            kind = act.get("type")
            content = (act.get("content") or "").strip()
            post_id = act.get("post_id")
            if kind == "nothing":
                done.append({"type": "nothing"})
                break
            if kind == "create_post" and content:
                new_id = self.add_post(round_no, day, hour, persona, persona.name, content)
                done.append({"type": "create_post", "post_id": new_id, "content": content})
            elif kind == "comment" and content and self.post_exists(post_id):
                reply_to = act.get("reply_to") if self.comment_on_post(act.get("reply_to"), post_id) else None
                new_id = self.db.run(
                    "INSERT INTO comments (simulation_id, post_id, reply_to, round, author_id, author_name, content) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (self.sim_id, post_id, reply_to, round_no, persona.id, persona.name, content[:MAX_TEXT]),
                )
                done.append({"type": "comment", "post_id": post_id, "comment_id": new_id, "content": content})
            elif kind == "like" and self.post_exists(post_id):
                exists = self.db.one("SELECT 1 FROM likes WHERE persona_id = ? AND post_id = ?", (persona.id, post_id))
                if not exists:
                    self.db.run(
                        "INSERT INTO likes (simulation_id, persona_id, post_id, round) VALUES (?, ?, ?, ?)",
                        (self.sim_id, persona.id, post_id, round_no),
                    )
                    done.append({"type": "like", "post_id": post_id})
            elif kind == "quote" and content and self.post_exists(post_id):
                new_id = self.add_post(round_no, day, hour, persona, persona.name, content, kind="quote", quote_of=post_id)
                done.append({"type": "quote", "post_id": new_id, "quote_of": post_id, "content": content})
            elif kind == "follow":
                target = self.by_handle.get(str(act.get("handle") or "").lower().strip())
                if target is None and act.get("handle"):
                    target = self.by_handle.get("@" + str(act["handle"]).lower().lstrip("@").strip())
                if target and target.id != persona.id:
                    self.db.run(
                        "INSERT OR IGNORE INTO follows (simulation_id, follower_id, followee_id, round) VALUES (?, ?, ?, ?)",
                        (self.sim_id, persona.id, target.id, round_no),
                    )
                    done.append({"type": "follow", "handle": target.handle()})
        return done

    def own_writing(self, persona: Persona, limit: int = 3) -> list[str]:
        rows = self.db.q(
            "SELECT content, round FROM posts WHERE simulation_id = ? AND author_id = ? "
            "UNION ALL SELECT content, round FROM comments WHERE simulation_id = ? AND author_id = ? "
            "ORDER BY round DESC LIMIT ?",
            (self.sim_id, persona.id, self.sim_id, persona.id, limit),
        )
        return [clip(r["content"], 220) for r in reversed(rows)]


def actions_json(done: list[dict]) -> str:
    return json.dumps(done, ensure_ascii=False)
