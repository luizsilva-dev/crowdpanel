from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .db import Database
from .engines import ENGINES, Engine, EngineError, get_engine
from .loader import ConfigError, dump_personas, load_personas, load_scenario
from .simulation import Runner, create_simulation, estimate, load_simulation


def say(msg: str) -> None:
    print(msg, flush=True)


def add_engine_args(p: argparse.ArgumentParser, required: bool = False) -> None:
    g = p.add_argument_group("engine")
    g.add_argument("--engine", choices=ENGINES, default=None if not required else "claude-code",
                   help="how to reach the model (default: claude-code)")
    g.add_argument("--model", help="model name for the engine")
    g.add_argument("--effort", help="reasoning effort for claude-code and anthropic (default: low)")
    g.add_argument("--base-url", help="openai engine: URL of an OpenAI-compatible server")
    g.add_argument("--api-key-env", help="name of the environment variable holding the API key")
    g.add_argument("--json-mode", choices=["schema", "object", "prompt"],
                   help="openai engine: how to ask for JSON (default: schema)")
    g.add_argument("--price-in", type=float, help="USD per million input tokens, for cost tracking")
    g.add_argument("--price-out", type=float, help="USD per million output tokens, for cost tracking")
    g.add_argument("--use-api-key", action="store_true", default=None,
                   help="claude-code: let the CLI use ANTHROPIC_API_KEY instead of your plan login")


def engine_options(args: argparse.Namespace) -> dict:
    opts = {
        "effort": args.effort, "base_url": args.base_url, "json_mode": args.json_mode,
        "price_in": args.price_in, "price_out": args.price_out, "use_api_key": args.use_api_key,
        "api_key_env": args.api_key_env,
    }
    return {k: v for k, v in opts.items() if v is not None}


def make_engine(name: str, model: str | None, options: dict) -> Engine:
    opts = dict(options)
    env_name = opts.pop("api_key_env", None)
    if env_name:
        key = os.environ.get(env_name)
        if not key:
            raise EngineError(f"environment variable {env_name} is not set", retryable=False)
        opts["api_key"] = key
    if name == "claude-code":
        opts = {k: v for k, v in opts.items() if k in ("effort", "use_api_key")}
    elif name == "codex":
        opts = {}
    elif name == "anthropic":
        opts = {k: v for k, v in opts.items() if k in ("effort", "api_key", "price_in", "price_out")}
    elif name == "openai":
        opts = {k: v for k, v in opts.items() if k in ("api_key", "base_url", "json_mode", "price_in", "price_out")}
    return get_engine(name, model, **opts)


def engine_for_sim(db: Database, sim_id: int, args: argparse.Namespace) -> Engine:
    sim = db.simulation(sim_id)
    stored = json.loads(sim["engine_options"])
    name = getattr(args, "engine", None) or sim["engine"]
    model = getattr(args, "model", None) or (sim["model"] if name == sim["engine"] else None)
    opts = stored if name == sim["engine"] else {}
    if hasattr(args, "effort"):
        opts = {**opts, **engine_options(args)}
    return make_engine(name, model, opts)


def parse_hours(text: str) -> tuple[int, int]:
    try:
        a, b = (int(x) for x in text.split("-", 1))
    except ValueError:
        raise ConfigError("--hours must look like 8-22")
    return a, b


def print_estimate(engine: Engine, personas, days: int, start: int, end: int, baseline: bool = True) -> None:
    est = estimate(personas, days, start, end, baseline)
    say(f"Engine: {engine.describe()}")
    say(f"About {est['calls']} model calls ({est['activations']} persona turns + surveys + report), "
        f"roughly {est['input_tokens'] / 1e6:.1f}M input and {est['output_tokens'] / 1e6:.2f}M output tokens.")
    price_in = getattr(engine, "price_in", None)
    price_out = getattr(engine, "price_out", None)
    if price_in is not None and price_out is not None:
        cost = (est["input_tokens"] * price_in + est["output_tokens"] * price_out) / 1e6
        say(f"Estimated cost without cache: about ${cost:.2f}.")
    elif engine.name in ("claude-code", "codex"):
        say("This uses your subscription limits, which are shared with your other sessions of that tool.")


def confirm(args: argparse.Namespace) -> bool:
    if args.yes or not sys.stdin.isatty():
        return True
    return input("Start? [y/N] ").strip().lower() in ("y", "yes")


def write_report(content: str, sim_id: int, out: str | None) -> Path:
    path = Path(out) if out else Path("reports") / f"simulation-{sim_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


async def finish(db: Database, engine: Engine, sim_id: int, args: argparse.Namespace) -> None:
    from .report import build_report
    from .survey import run_survey

    if not args.no_survey:
        say("Running the survey...")
        await run_survey(db, engine, sim_id, progress=say)
        if not args.no_report:
            say("Writing the report...")
            content = await build_report(db, engine, sim_id, language=getattr(args, "lang", None))
            say(f"Report saved to {write_report(content, sim_id, getattr(args, 'output', None))}")


async def cmd_run(args: argparse.Namespace, db: Database) -> int:
    scenario = load_scenario(args.scenario)
    personas = load_personas(args.personas)
    start, end = parse_hours(args.hours)
    offers = [o.strip() for o in args.offer.split(",")] if args.offer else None
    opts = engine_options(args)
    engine = make_engine(args.engine or "claude-code", args.model, opts)
    try:
        print_estimate(engine, personas, args.days, start, end, not args.no_baseline)
        if not confirm(args):
            return 1
        sim_id = create_simulation(
            db, scenario, personas, offers, engine, name=args.name or "", days=args.days, start_hour=start,
            end_hour=end, seed=args.seed, parallel=args.parallel, feed_size=args.feed_size,
            max_actions=args.max_actions, max_calls=args.max_calls, max_cost=args.max_cost, engine_options=opts,
            baseline=not args.no_baseline,
        )
        say(f"Simulation {sim_id} created.")
        status = await Runner(db, engine, say).run(sim_id)
        if status == "done":
            await finish(db, engine, sim_id, args)
        return 0 if status == "done" else 2
    finally:
        await engine.close()


async def cmd_ab(args: argparse.Namespace, db: Database) -> int:
    from .report import compare_markdown

    scenario = load_scenario(args.scenario)
    offer_ids = [o.strip() for o in args.offers.split(",")]
    if len(offer_ids) != 2:
        raise ConfigError("--offers needs exactly two offer ids, like a,b")
    start, end = parse_hours(args.hours)
    opts = engine_options(args)
    engine = make_engine(args.engine or "claude-code", args.model, opts)
    seed = args.seed if args.seed is not None else int(datetime.now().timestamp()) % 100000
    try:
        personas = load_personas(args.personas)
        print_estimate(engine, personas * 2, args.days, start, end, not args.no_baseline)
        if not confirm(args):
            return 1
        ids = []
        for offer in offer_ids:
            personas = load_personas(args.personas)  # fresh copies: each run gets its own rows
            sim_id = create_simulation(
                db, scenario, personas, [offer], engine, days=args.days, start_hour=start, end_hour=end, seed=seed,
                parallel=args.parallel, feed_size=args.feed_size, max_actions=args.max_actions,
                max_calls=args.max_calls, max_cost=args.max_cost, engine_options=opts,
                baseline=not args.no_baseline,
            )
            say(f"Simulation {sim_id}: offer {offer}")
            status = await Runner(db, engine, say).run(sim_id)
            if status != "done":
                say(f"Simulation {sim_id} did not finish ({status}); resume it and run compare later.")
                return 2
            await finish(db, engine, sim_id, args)
            ids.append(sim_id)
        content = compare_markdown(db, ids[0], ids[1])
        path = write_report(content, ids[0], f"reports/compare-{ids[0]}-{ids[1]}.md")
        say(f"Comparison saved to {path}")
        return 0
    finally:
        await engine.close()


async def cmd_resume(args: argparse.Namespace, db: Database) -> int:
    sim = db.simulation(args.id)
    if args.max_calls is not None or args.max_cost is not None:
        db.run("UPDATE simulations SET max_calls = COALESCE(?, max_calls), max_cost = COALESCE(?, max_cost) WHERE id = ?",
               (args.max_calls, args.max_cost, args.id))
        db.commit()
    engine = engine_for_sim(db, args.id, args)
    try:
        if sim["status"] == "done":
            say("Simulation already finished; running survey and report if missing.")
            status = "done"
        else:
            status = await Runner(db, engine, say).run(args.id)
        if status == "done":
            await finish(db, engine, args.id, args)
        return 0 if status == "done" else 2
    finally:
        await engine.close()


async def cmd_survey(args: argparse.Namespace, db: Database) -> int:
    from .survey import run_survey

    engine = engine_for_sim(db, args.id, args)
    try:
        await run_survey(db, engine, args.id, redo=args.redo, progress=say)
    finally:
        await engine.close()
    return 0


async def cmd_report(args: argparse.Namespace, db: Database) -> int:
    from .report import build_report

    engine = engine_for_sim(db, args.id, args)
    try:
        content = await build_report(db, engine, args.id, language=args.lang, narrative=not args.no_narrative)
    finally:
        await engine.close()
    say(f"Report saved to {write_report(content, args.id, args.output)}")
    return 0


async def cmd_ask(args: argparse.Namespace, db: Database) -> int:
    from .interview import ask

    engine = engine_for_sim(db, args.id, args)
    try:
        answers = await ask(db, engine, args.id, args.question, args.persona or None)
    finally:
        await engine.close()
    for p, answer in answers:
        say(f"\n{p.name} ({p.segment}):\n{answer}")
    return 0


async def cmd_personas(args: argparse.Namespace, db: Database) -> int:
    from .personas_gen import generate_personas

    scenario = load_scenario(args.scenario)
    engine = make_engine(args.engine or "claude-code", args.model, engine_options(args))
    try:
        personas = await generate_personas(db, engine, scenario, args.count, args.spec or "", progress=say)
    finally:
        await engine.close()
    dump_personas(personas, args.output)
    say(f"Saved {len(personas)} personas to {args.output}. Review and edit them before running.")
    return 0


async def cmd_check(args: argparse.Namespace, db: Database) -> int:
    engine = make_engine(args.engine or "claude-code", args.model, engine_options(args))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "word": {"type": "string"}},
              "required": ["ok", "word"], "additionalProperties": False}
    try:
        res = await engine.generate("Answer briefly.", "Reply with ok=true and any short word.", schema, max_tokens=500)
    finally:
        await engine.close()
    say(f"{engine.describe()} works: {res.data} in {res.seconds:.1f}s, "
        f"{res.usage.input_tokens + res.usage.cached_input_tokens + res.usage.cache_write_tokens} input tokens.")
    return 0


def cmd_list(args: argparse.Namespace, db: Database) -> int:
    rows = db.q("SELECT * FROM simulations ORDER BY id DESC LIMIT ?", (args.limit,))
    if not rows:
        say("No simulations yet.")
    for r in rows:
        when = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M")
        extra = f"  ({r['error']})" if r["error"] else ""
        say(f"{r['id']:>4}  {when}  {r['status']:<8} {r['current_round']:>3}/{r['total_rounds']:<3} "
            f"{r['engine']}:{r['model']}  {r['name']}{extra}")
    return 0


def cmd_show(args: argparse.Namespace, db: Database) -> int:
    from .export import transcript_markdown

    sim = db.simulation(args.id)
    spent = db.spend(args.id)
    say(f"{sim['name']}\nstatus: {sim['status']}  round {sim['current_round']}/{sim['total_rounds']}  "
        f"engine: {sim['engine']} ({sim['model']})")
    if sim["error"]:
        say(f"note: {sim['error']}")
    say(f"calls: {spent.get('calls', 0)} ({spent.get('errors') or 0} failed), "
        f"tokens in/out: {spent.get('input', 0) + spent.get('cached', 0):,}/{spent.get('output', 0):,}"
        + (f", cost ${spent['cost']:.2f}" if spent.get("cost") else ""))
    if args.feed:
        say("")
        say(transcript_markdown(db, args.id))
    return 0


def cmd_stop(args: argparse.Namespace, db: Database) -> int:
    sim = db.simulation(args.id)
    if sim["status"] != "running":
        say(f"Simulation {args.id} is {sim['status']}, nothing to stop.")
        return 0
    db.set_status(args.id, "stopping")
    say("Asked the simulation to stop after the current round.")
    return 0


def cmd_compare(args: argparse.Namespace, db: Database) -> int:
    from .report import compare_markdown

    content = compare_markdown(db, args.a, args.b)
    if args.output:
        say(f"Saved to {write_report(content, args.a, args.output)}")
    else:
        say(content)
    return 0


def cmd_export(args: argparse.Namespace, db: Database) -> int:
    from .export import export_json, transcript_markdown

    content = export_json(db, args.id) if args.format == "json" else transcript_markdown(db, args.id)
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
        say(f"Saved to {args.output}")
    else:
        say(content)
    return 0


def cmd_web(args: argparse.Namespace, db: Database) -> int:
    try:
        import uvicorn
        from .web.app import create_app
    except ImportError:
        raise ConfigError("the web interface needs: pip install 'crowdpanel[web]'")
    db.close()
    uvicorn.run(create_app(args.db), host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crowdpanel", description="Simulate customers discussing your offer, then survey them.")
    p.add_argument("--version", action="version", version=f"crowdpanel {__version__}")
    p.add_argument("--db", default=os.environ.get("CROWDPANEL_DB", "crowdpanel.db"), help="SQLite file (default: crowdpanel.db)")
    sub = p.add_subparsers(dest="command", required=True)

    def run_options(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("scenario", help="scenario YAML file")
        sp.add_argument("personas", help="personas YAML file")
        sp.add_argument("--days", type=int, default=3, help="simulated days (default: 3)")
        sp.add_argument("--hours", default="8-22", help="active hours each day (default: 8-22)")
        sp.add_argument("--seed", type=int, help="random seed, for repeatable runs")
        sp.add_argument("--parallel", type=int, help="persona calls at the same time")
        sp.add_argument("--feed-size", type=int, default=5, help="posts each persona sees per turn (default: 5)")
        sp.add_argument("--max-actions", type=int, default=3, help="actions per persona turn (default: 3)")
        sp.add_argument("--max-calls", type=int, help="pause when this many model calls were made")
        sp.add_argument("--max-cost", type=float, help="pause when the tracked cost reaches this many USD")
        sp.add_argument("--no-survey", action="store_true", help="skip the final survey and report")
        sp.add_argument("--no-report", action="store_true", help="skip the report")
        sp.add_argument("--no-baseline", action="store_true",
                        help="skip the first-impression survey taken before the discussion")
        sp.add_argument("--lang", help="report language (default: the scenario language)")
        sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
        add_engine_args(sp)

    sp = sub.add_parser("run", help="run a new simulation")
    run_options(sp)
    sp.add_argument("--offer", help="offer id(s) to present, comma separated (default: all)")
    sp.add_argument("--name", help="name for this simulation")
    sp.add_argument("-o", "--output", help="report file (default: reports/simulation-ID.md)")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("ab", help="run the same crowd twice, once per offer, and compare")
    run_options(sp)
    sp.add_argument("--offers", required=True, help="two offer ids, like a,b")
    sp.set_defaults(func=cmd_ab)

    sp = sub.add_parser("resume", help="continue a paused or failed simulation")
    sp.add_argument("id", type=int)
    sp.add_argument("--max-calls", type=int, help="new call limit")
    sp.add_argument("--max-cost", type=float, help="new cost limit in USD")
    sp.add_argument("--no-survey", action="store_true")
    sp.add_argument("--no-report", action="store_true")
    sp.add_argument("--lang")
    sp.add_argument("-o", "--output")
    add_engine_args(sp)
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("survey", help="(re)run the final survey")
    sp.add_argument("id", type=int)
    sp.add_argument("--redo", action="store_true", help="ask everyone again")
    add_engine_args(sp)
    sp.set_defaults(func=cmd_survey)

    sp = sub.add_parser("report", help="(re)build the report")
    sp.add_argument("id", type=int)
    sp.add_argument("--lang", help="report language")
    sp.add_argument("--no-narrative", action="store_true", help="only numbers and quotes, no model call")
    sp.add_argument("-o", "--output")
    add_engine_args(sp)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("ask", help="interview personas after a run")
    sp.add_argument("id", type=int)
    sp.add_argument("question")
    sp.add_argument("--persona", action="append", help="name or @handle (repeat for several; default: everyone)")
    add_engine_args(sp)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("personas", help="generate a personas file with the model")
    sp.add_argument("scenario")
    sp.add_argument("--count", type=int, default=12)
    sp.add_argument("--spec", help="who should be in the crowd, in plain words")
    sp.add_argument("-o", "--output", default="personas.yaml")
    add_engine_args(sp)
    sp.set_defaults(func=cmd_personas)

    sp = sub.add_parser("check", help="make one tiny call to test an engine")
    add_engine_args(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("list", help="list simulations")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="status of a simulation")
    sp.add_argument("id", type=int)
    sp.add_argument("--feed", action="store_true", help="print the whole discussion")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("stop", help="stop a running simulation after the current round")
    sp.add_argument("id", type=int)
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("compare", help="compare the survey of two simulations")
    sp.add_argument("a", type=int)
    sp.add_argument("b", type=int)
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("export", help="export the discussion")
    sp.add_argument("id", type=int)
    sp.add_argument("--format", choices=["md", "json"], default="md")
    sp.add_argument("-o", "--output")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("web", help="local web interface")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.set_defaults(func=cmd_web)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(args.db)
    try:
        result = args.func(args, db)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return int(result or 0)
    except KeyboardInterrupt:
        say("\nInterrupted. The simulation is paused; run `crowdpanel resume ID` to continue.")
        return 130
    except (ConfigError, EngineError, LookupError, ValueError) as e:
        say(f"error: {e}")
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
