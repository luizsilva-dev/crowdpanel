# crowdpanel

Put your offer in front of a crowd of simulated customers before you build it.

crowdpanel creates a small online community of personas (each one with a name, a background, a budget, a temperament and their own memory), posts your offer in it and lets them talk for a few simulated days: they comment, argue, compare, do the math, react to news and change their minds. Your company can take part too, answering only with the facts you give it, improvising like a founder, or staying silent. At the end every persona answers a short survey, and a report sums up what they liked, what held them back and which angles you had not considered.

Before the discussion each persona gives a first impression alone, so the report can show who changed their mind and what moved them, and flag when the crowd simply converged.

The point is not to predict sales. Simulated people are not real customers, and the same crowd can answer differently with another model. The point is to see the decision from many sides before you commit: the objection you did not think of, the segment that does the math differently, the question everyone keeps asking.

```
$ crowdpanel run examples/coffee-subscription/scenario.yaml examples/coffee-subscription/personas.yaml
Engine: claude-code (haiku)
About 133 model calls (112 persona turns + surveys + report), roughly 0.4M input and 0.05M output tokens.
Start? [y/N] y
Simulation 1 created.
Day 1 08:00  2 awake, 3 actions  [1/45]
Day 1 09:00  3 awake, 5 actions  [2/45]
...
Report saved to reports/simulation-1.md
```

## Credit

crowdpanel was inspired by [MiroFish](https://github.com/666ghj/MiroFish), a swarm intelligence engine that builds a parallel world of agents from a document, and by the [OASIS](https://github.com/camel-ai/oasis) social simulator it runs on. crowdpanel is an independent implementation written from scratch for one job, testing a business offer, and shares no code with either project.

What it does differently:

| | MiroFish | crowdpanel |
|---|---|---|
| Who takes part | Entities extracted from a document by the model; people mentioned only in passing can be dropped | The personas you list (or generate and review), exactly |
| The company | Becomes an agent like any other and can promise things on your behalf | Answers only from facts you wrote by default; improvising is opt-in and every reply is listed in the report |
| Memory | Every past turn is resent on every call, so prompts and cost keep growing | Current opinion, last 8 notes and the last 3 things each persona wrote: the prompt stays the same size |
| Platforms | Twitter-like and Reddit-like in parallel, two calls per action | One forum, one call per turn |
| Simulated time | Starts at midnight, many empty rounds | Only the hours you choose |
| Output | Long narrative report | Structured survey per persona before and after the discussion, tallied by segment, who changed their mind, a report and an A/B comparison |
| Cost control | None | Estimate before running, call and cost limits that pause the run |
| Stop and resume | Stopping requires starting over | Pause any time, resume from the same round |
| Failures | Some errors are swallowed and the step reports success | Every call is logged; fatal errors stop the run with the reason |
| Models | One OpenAI-compatible endpoint | Claude Code or Codex with your own subscription, Anthropic API, OpenAI or any OpenAI-compatible API |
| Extra services | Neo4j, Ollama, embeddings, a Hugging Face model for recommendations | SQLite only |

## Install

Python 3.10 or newer.

```bash
pip install "crowdpanel[all] @ git+https://github.com/luizsilva-dev/crowdpanel"
```

Or from a clone:

```bash
git clone https://github.com/luizsilva-dev/crowdpanel
cd crowdpanel
pip install -e ".[all]"
```

The extras are optional: `anthropic` and `openai` install the API SDKs, `web` installs the local web interface. The core only needs PyYAML.

## Choose how to reach a model

| Engine | What you need | Who pays |
|---|---|---|
| `claude-code` (default) | [Claude Code](https://code.claude.com) installed and logged in | Your Claude plan limits |
| `codex` | [Codex CLI](https://github.com/openai/codex) installed and logged in (`codex login`) | Your ChatGPT plan limits |
| `anthropic` | `ANTHROPIC_API_KEY` | API usage |
| `openai` | `OPENAI_API_KEY`, or any OpenAI-compatible server with `--base-url` | API usage |
| `fake` | nothing | free, random answers for testing |

Test an engine with one tiny call:

```bash
crowdpanel check --engine claude-code
crowdpanel check --engine anthropic --model claude-haiku-5-5
crowdpanel check --engine openai --model YOUR_MODEL
crowdpanel check --engine openai --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY --model some/model
crowdpanel check --engine openai --base-url http://localhost:11434/v1 --api-key-env OLLAMA_KEY --model llama3.1 --json-mode prompt
```

The subscription engines run the official CLI on your machine, under your own login. They are meant for your personal use within each provider's terms; if you build something for other people on top of crowdpanel, use the API engines. Details in [docs/engines.md](docs/engines.md).

## Quick start

1. Copy the example and edit it:

   ```bash
   cp -r examples/coffee-subscription my-test
   ```

   `scenario.yaml` describes the place, the context and the offers. `personas.yaml` lists the people. See [docs/scenario-format.md](docs/scenario-format.md).

2. Or generate personas and review them:

   ```bash
   crowdpanel personas my-test/scenario.yaml --count 16 \
     --spec "home coffee drinkers in the US, from budget buyers to espresso nerds, a few small businesses" \
     -o my-test/personas.yaml
   ```

3. Run:

   ```bash
   crowdpanel run my-test/scenario.yaml my-test/personas.yaml --days 3
   ```

4. Read `reports/simulation-1.md`, then dig in:

   ```bash
   crowdpanel show 1 --feed                     # the whole discussion
   crowdpanel ask 1 "What would make you pay for a year upfront?"
   crowdpanel ask 1 "Why did you say no?" --persona "Priya Patel"
   ```

5. Compare two offers with the same crowd:

   ```bash
   crowdpanel ab my-test/scenario.yaml my-test/personas.yaml --offers a,b
   ```

## Web interface

```bash
crowdpanel web          # http://127.0.0.1:8765
```

Start simulations from your YAML files, follow the discussion live, filter by persona or segment, stop and resume, see the survey charts and the report, and interview personas. It has no login, so keep it on localhost.

## Cost and limits

Before every run crowdpanel prints an estimate of calls and tokens. Set limits and the run pauses when it reaches them:

```bash
crowdpanel run scenario.yaml personas.yaml --engine anthropic --max-cost 2.50
crowdpanel run scenario.yaml personas.yaml --max-calls 150
crowdpanel resume 1 --max-calls 300      # raise the limit and continue
```

Cost in dollars is tracked for the `anthropic` engine (built-in prices, override with `--price-in` and `--price-out`) and for `openai` when you pass prices. Subscription engines report tokens only.

With 10 personas, 3 days and Claude Haiku, a run makes about 130 calls of roughly 3,000 input tokens each.

## Commands

| Command | What it does |
|---|---|
| `run SCENARIO PERSONAS` | Create and run a simulation, then survey and report |
| `ab SCENARIO PERSONAS --offers a,b` | Same crowd and seed, once per offer, plus a comparison |
| `resume ID` | Continue a paused, stopped or failed simulation |
| `stop ID` | Stop a running simulation after the current round |
| `survey ID [--redo]` | Run (or rerun) the final survey |
| `report ID [--lang] [--no-narrative]` | Rebuild the report |
| `ask ID "question" [--persona NAME]` | Interview one, several or all personas |
| `personas SCENARIO --count N --spec "..."` | Generate a personas file |
| `list`, `show ID [--feed]` | List simulations, show status or the discussion |
| `compare A B` | Compare the surveys of two simulations |
| `export ID --format md\|json` | Export everything |
| `check` | Test an engine |
| `web` | Local web interface |

Run `crowdpanel COMMAND --help` for all options. Everything is stored in `crowdpanel.db` (change it with `--db` or `CROWDPANEL_DB`).

## How it works

Each simulated hour some personas wake up, based on their active hours and how active they are. Each one gets its own feed (the pinned offer plus the posts it has not seen, from people it follows and the busiest threads), its memory and its current opinion, and decides what to do: post, comment, reply, like, quote, follow or nothing. Personas never see each other's private thoughts. Each persona answers the survey twice, alone before the discussion and again after the last round. Full description in [docs/how-it-works.md](docs/how-it-works.md).

## Limits worth knowing

- Personas are played by a language model. They can be more articulate, more reasonable and more alike than real people, and they inherit the model's biases.
- A small crowd (10 to 30 personas) explores reactions; it is not a statistical sample.
- Personas influence each other. When everyone agrees at the end, check the first impressions: it may be groupthink.
- Results change with the model, the seed and how you describe the personas. Run more than once before trusting a pattern.
- Use it to find questions worth asking real customers, not to replace them.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The `fake` engine makes the whole pipeline testable offline.

## License

MIT
