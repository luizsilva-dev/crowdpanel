from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..cli import make_engine
from ..db import Database
from ..engines import ENGINES, EngineError
from ..loader import ConfigError, parse_personas, parse_scenario
from ..simulation import Runner, create_simulation, load_simulation
from ..survey import aggregate, opinion_shifts

HERE = Path(__file__).parent


def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="crowdpanel", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    tasks: dict[int, asyncio.Task] = {}
    logs: dict[int, list[str]] = {}

    def open_db() -> Database:
        return Database(db_path)

    def engine_for(sim: Any):
        return make_engine(sim["engine"], sim["model"], json.loads(sim["engine_options"]))

    async def run_full(sim_id: int, survey: bool = True) -> None:
        from ..report import build_report
        from ..survey import run_survey

        log = logs.setdefault(sim_id, [])
        db = open_db()
        engine = None
        try:
            engine = engine_for(db.simulation(sim_id))
            status = await Runner(db, engine, log.append).run(sim_id)
            if status == "done" and survey:
                log.append("Running the survey...")
                await run_survey(db, engine, sim_id, progress=log.append)
                log.append("Writing the report...")
                await build_report(db, engine, sim_id)
                log.append("Report ready.")
        except (EngineError, ConfigError) as e:
            log.append(f"Error: {e}")
        except Exception as e:  # keep the server alive and show what broke
            log.append(f"Unexpected error: {e!r}")
            db.set_status(sim_id, "failed", repr(e)[:500])
        finally:
            if engine:
                await engine.close()
            db.close()
            tasks.pop(sim_id, None)

    def start(sim_id: int, survey: bool = True) -> None:
        if sim_id not in tasks:
            tasks[sim_id] = asyncio.create_task(run_full(sim_id, survey))

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        db = open_db()
        try:
            sims = [dict(r) for r in db.q("SELECT * FROM simulations ORDER BY id DESC LIMIT 50")]
        finally:
            db.close()
        return templates.TemplateResponse(request, "home.html", {"sims": sims, "engines": ENGINES})

    @app.post("/new")
    async def new(request: Request, scenario: UploadFile, personas: UploadFile, engine: str = Form("claude-code"),
                  model: str = Form(""), offers: str = Form(""), days: int = Form(3), hours: str = Form("8-22"),
                  max_calls: str = Form(""), max_cost: str = Form(""), base_url: str = Form(""),
                  api_key_env: str = Form("")):
        import yaml

        try:
            sc = parse_scenario(yaml.safe_load(await scenario.read()))
            ps = parse_personas(yaml.safe_load(await personas.read()))
            start_h, end_h = (int(x) for x in hours.split("-", 1))
            opts = {k: v for k, v in {"base_url": base_url or None, "api_key_env": api_key_env or None}.items() if v}
            eng = make_engine(engine, model or None, opts)
            offer_ids = [o.strip() for o in offers.split(",") if o.strip()] or None
            db = open_db()
            try:
                sim_id = create_simulation(
                    db, sc, ps, offer_ids, eng, days=days, start_hour=start_h, end_hour=end_h,
                    max_calls=int(max_calls) if max_calls else None,
                    max_cost=float(max_cost) if max_cost else None, engine_options=opts,
                )
            finally:
                db.close()
            await eng.close()
        except (ConfigError, EngineError, ValueError, KeyError, yaml.YAMLError) as e:
            return templates.TemplateResponse(request, "error.html", {"message": str(e)}, status_code=400)
        start(sim_id)
        return RedirectResponse(f"/sim/{sim_id}", status_code=303)

    @app.get("/sim/{sim_id}", response_class=HTMLResponse)
    async def sim_page(request: Request, sim_id: int, persona: str = "", segment: str = ""):
        db = open_db()
        try:
            sim, scenario, offers, personas = load_simulation(db, sim_id)
            spent = db.spend(sim_id)
            from ..simulation import load_brand

            brand = load_brand(db, sim_id)
            by_id = {p.id: p for p in personas + ([brand] if brand else [])}
            posts = []
            for p in db.q("SELECT * FROM posts WHERE simulation_id = ? ORDER BY id DESC", (sim_id,)):
                author = by_id.get(p["author_id"])
                comments = [dict(c) for c in db.q("SELECT * FROM comments WHERE post_id = ? ORDER BY id", (p["id"],))]
                likes = db.one("SELECT COUNT(*) AS n FROM likes WHERE post_id = ?", (p["id"],))["n"]
                involved = {p["author_name"]} | {c["author_name"] for c in comments}
                if persona and persona not in involved:
                    continue
                if segment and not any(by_id.get(c["author_id"]) and by_id[c["author_id"]].segment == segment
                                       for c in comments) and not (author and author.segment == segment):
                    continue
                posts.append({**dict(p), "comments": comments, "likes": likes,
                              "segment": author.segment if author else ""})
            people = [
                {**dict(r), "segment": by_id[r["id"]].segment}
                for r in db.q("SELECT id, name, stance, activations FROM personas WHERE simulation_id = ? "
                              "AND role = 'customer' ORDER BY name", (sim_id,))
            ]
            interviews = [dict(r) for r in db.q(
                "SELECT i.*, p.name FROM interviews i JOIN personas p ON p.id = i.persona_id "
                "WHERE i.simulation_id = ? ORDER BY i.id DESC LIMIT 30", (sim_id,))]
            has_survey = db.one("SELECT 1 FROM survey WHERE simulation_id = ? AND phase = 'after'", (sim_id,)) is not None
            report = db.one("SELECT content FROM reports WHERE simulation_id = ?", (sim_id,))
        finally:
            db.close()
        return templates.TemplateResponse(request, "sim.html", {
            "sim": dict(sim), "scenario": scenario, "offers": offers, "spent": spent, "posts": posts,
            "people": people, "segments": sorted({p.segment for p in personas}), "persona": persona,
            "segment": segment, "running": sim_id in tasks, "log": logs.get(sim_id, [])[-12:],
            "has_survey": has_survey, "has_report": report is not None, "interviews": interviews,
        })

    @app.get("/sim/{sim_id}/results", response_class=HTMLResponse)
    async def results(request: Request, sim_id: int):
        db = open_db()
        try:
            sim = dict(db.simulation(sim_id))
            agg = aggregate(db, sim_id)
            before = aggregate(db, sim_id, phase="before")
            shifts = opinion_shifts(db, sim_id)
            report = db.one("SELECT content FROM reports WHERE simulation_id = ?", (sim_id,))
        finally:
            db.close()
        first = {q["id"]: q for q in before["questions"]} if before["respondents"] else {}
        return templates.TemplateResponse(request, "results.html", {
            "sim": sim, "agg": agg, "first": first, "shifts": shifts,
            "report": report["content"] if report else None,
        })

    @app.post("/sim/{sim_id}/stop")
    async def stop(sim_id: int):
        db = open_db()
        try:
            if db.simulation(sim_id)["status"] == "running":
                db.set_status(sim_id, "stopping")
        finally:
            db.close()
        return RedirectResponse(f"/sim/{sim_id}", status_code=303)

    @app.post("/sim/{sim_id}/resume")
    async def resume(sim_id: int):
        start(sim_id)
        return RedirectResponse(f"/sim/{sim_id}", status_code=303)

    @app.post("/sim/{sim_id}/ask")
    async def ask_view(request: Request, sim_id: int, question: str = Form(...), persona: str = Form("")):
        from ..interview import ask

        db = open_db()
        engine = None
        try:
            engine = engine_for(db.simulation(sim_id))
            await ask(db, engine, sim_id, question, [persona] if persona else None)
        except (EngineError, LookupError) as e:
            return templates.TemplateResponse(request, "error.html", {"message": str(e)}, status_code=400)
        finally:
            if engine:
                await engine.close()
            db.close()
        return RedirectResponse(f"/sim/{sim_id}#interviews", status_code=303)

    return app
