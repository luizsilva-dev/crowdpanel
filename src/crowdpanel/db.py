from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS simulations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    scenario_json TEXT NOT NULL,
    offer_ids TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT NOT NULL,
    engine_options TEXT NOT NULL DEFAULT '{}',
    days INTEGER NOT NULL,
    start_hour INTEGER NOT NULL,
    end_hour INTEGER NOT NULL,
    seed INTEGER NOT NULL,
    parallel INTEGER NOT NULL,
    feed_size INTEGER NOT NULL DEFAULT 5,
    max_actions INTEGER NOT NULL DEFAULT 3,
    max_calls INTEGER,
    max_cost REAL,
    baseline INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ready',
    current_round INTEGER NOT NULL DEFAULT 0,
    total_rounds INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id INTEGER NOT NULL REFERENCES simulations(id),
    name TEXT NOT NULL,
    data_json TEXT NOT NULL,
    stance TEXT NOT NULL DEFAULT '',
    notes_json TEXT NOT NULL DEFAULT '[]',
    activations INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'customer'
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id INTEGER NOT NULL REFERENCES simulations(id),
    round INTEGER NOT NULL,
    day INTEGER NOT NULL,
    hour INTEGER NOT NULL,
    author_id INTEGER,
    author_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'post',
    content TEXT NOT NULL,
    quote_of INTEGER
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id INTEGER NOT NULL REFERENCES simulations(id),
    post_id INTEGER NOT NULL REFERENCES posts(id),
    reply_to INTEGER,
    round INTEGER NOT NULL,
    author_id INTEGER,
    author_name TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS likes (
    simulation_id INTEGER NOT NULL,
    persona_id INTEGER NOT NULL,
    post_id INTEGER,
    comment_id INTEGER,
    round INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS follows (
    simulation_id INTEGER NOT NULL,
    follower_id INTEGER NOT NULL,
    followee_id INTEGER NOT NULL,
    round INTEGER NOT NULL,
    PRIMARY KEY (simulation_id, follower_id, followee_id)
);
CREATE TABLE IF NOT EXISTS seen (
    simulation_id INTEGER NOT NULL,
    persona_id INTEGER NOT NULL,
    post_id INTEGER NOT NULL,
    comments_seen INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (simulation_id, persona_id, post_id)
);
CREATE TABLE IF NOT EXISTS activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id INTEGER NOT NULL,
    round INTEGER NOT NULL,
    persona_id INTEGER NOT NULL,
    thought TEXT NOT NULL DEFAULT '',
    stance TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    actions_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id INTEGER,
    round INTEGER,
    persona_id INTEGER,
    purpose TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    seconds REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS survey (
    simulation_id INTEGER NOT NULL,
    persona_id INTEGER NOT NULL,
    phase TEXT NOT NULL DEFAULT 'after',
    answers_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (simulation_id, persona_id, phase)
);
CREATE TABLE IF NOT EXISTS interviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    simulation_id INTEGER NOT NULL,
    persona_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    simulation_id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_sim ON posts (simulation_id, round);
CREATE INDEX IF NOT EXISTS comments_post ON comments (post_id);
CREATE INDEX IF NOT EXISTS calls_sim ON calls (simulation_id);
"""


class Database:
    def __init__(self, path: str | Path = "crowdpanel.db"):
        self.path = str(path)
        # several runs (and the web interface) can share one file; wait for a lock instead of failing
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Adds columns introduced after a database file was created."""
        added = {
            "personas": [("role", "TEXT NOT NULL DEFAULT 'customer'")],
            "simulations": [("baseline", "INTEGER NOT NULL DEFAULT 1")],
        }
        for table, columns in added.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def q(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple | dict = ()) -> int:
        cur = self.conn.execute(sql, params)
        return cur.lastrowid or 0

    def commit(self) -> None:
        self.conn.commit()

    def simulation(self, sim_id: int) -> sqlite3.Row:
        row = self.one("SELECT * FROM simulations WHERE id = ?", (sim_id,))
        if row is None:
            raise LookupError(f"simulation {sim_id} not found in {self.path}")
        return row

    def set_status(self, sim_id: int, status: str, error: str | None = None) -> None:
        self.run("UPDATE simulations SET status = ?, error = ? WHERE id = ?", (status, error, sim_id))
        self.commit()

    def log_call(self, sim_id: int | None, round_no: int | None, persona_id: int | None, purpose: str,
                 engine: str, model: str, usage: Any = None, seconds: float = 0.0, error: str | None = None) -> None:
        self.run(
            "INSERT INTO calls (simulation_id, round, persona_id, purpose, engine, model, input_tokens, "
            "cached_input_tokens, cache_write_tokens, output_tokens, cost_usd, seconds, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sim_id, round_no, persona_id, purpose, engine, model,
                getattr(usage, "input_tokens", 0), getattr(usage, "cached_input_tokens", 0),
                getattr(usage, "cache_write_tokens", 0), getattr(usage, "output_tokens", 0),
                getattr(usage, "cost_usd", None), seconds, error, time.time(),
            ),
        )

    def spend(self, sim_id: int) -> dict[str, Any]:
        row = self.one(
            "SELECT COUNT(*) AS calls, SUM(error IS NOT NULL) AS errors, COALESCE(SUM(input_tokens), 0) AS input, "
            "COALESCE(SUM(cached_input_tokens), 0) AS cached, COALESCE(SUM(cache_write_tokens), 0) AS cache_write, "
            "COALESCE(SUM(output_tokens), 0) AS output, SUM(cost_usd) AS cost, COALESCE(SUM(seconds), 0) AS seconds "
            "FROM calls WHERE simulation_id = ?",
            (sim_id,),
        )
        return {k: row[k] for k in row.keys()} if row else {}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
