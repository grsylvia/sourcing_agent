# Sourcing Agent

BOM in → CBOM out (BOM + vendor + pricing), sourced from approved suppliers.

```
BOM ──▶ split by category ──▶ worker per category (parallel) ──▶ lowest price ──▶ CBOM
                                 └─ web search: approved suppliers only              │ not_found rows
                                                                                     ▼
suppliers.toml ◀── you approve ◀── trial on real rows ◀── free screen ◀── scout per category (open web)
```

| Ability | Skill | Commands |
| --- | --- | --- |
| Personal startup | `/source-bom` | `startup`, `memory` ([guide](docs/PERSONAL_SETUP.md)) |
| BOM → CBOM | `/source-bom` | `estimate`, `run`, `suppliers` |
| Find and verify suppliers | `/find-suppliers` | `discover`, `trial`, `candidates` ([docs/DISCOVERY.md](docs/DISCOVERY.md)) |

## Setup

| Step | Command |
| --- | --- |
| Create venv | `python3 -m venv .venv` |
| Activate | `source .venv/bin/activate` |
| Install | `pip install -e .` |
| Anthropic key | `export ANTHROPIC_API_KEY=...` |
| OpenAI key | `export OPENAI_API_KEY=...` |

## Usage

```
# Inspect folder choices and current suppliers/discovery sources without API calls.
sourcing startup
# Initialize chosen folders; acknowledge sources separately after reviewing the displayed revision.
sourcing startup --templates ~/sourcing --boms ~/sourcing --cboms ~/sourcing
sourcing estimate my_bom.xlsx --compare  # compare providers and supported modes (no API calls)
sourcing estimate my_bom.xlsx --provider openai --model gpt-6-luna
sourcing run my_bom.xlsx --provider openai --model gpt-6-sol --live
sourcing run my_bom.xlsx --out cbom.csv  # batch (default)
sourcing run my_bom.xlsx --live          # live
sourcing suppliers cbom.csv              # supplier win rates and repeated search failures
sourcing discover cbom.csv --estimate    # plan + cost of scouting categories with unsourced rows
sourcing discover cbom.csv               # scout, screen, save candidates
sourcing trial vxb.com --cbom cbom.csv   # quote sample rows on one candidate only
sourcing candidates --approve vxb.com    # add a trialed candidate to suppliers.toml
sourcing learn                           # what logged runs teach: spend by meter/category/settings, learned token profile, next steps
```

| BOM input | Detail |
| --- | --- |
| CSV | [`examples/bom.csv`](examples/bom.csv) |
| Excel | Copy [`templates/bom_template.xlsx`](templates/bom_template.xlsx) to your chosen template folder, fill the `BOM` sheet |

| Flag | Effect |
| --- | --- |
| *(default)* | Batch API: 50% off tokens; usually under 1 h, max 24 h |
| `--live` | Full price, results in minutes |
| `--max-age DAYS` | Reuse cached quotes up to this age (default 7; `0` = re-source all) |
| `--provider anthropic\|openai` | Anthropic (default) or OpenAI; OpenAI requires `--live` |
| `--model MODEL` | First pass: defaults to Sonnet 5 or GPT-6 Sol |
| `--escalation-model MODEL` | Retry within the selected provider: defaults to Opus 5.5 or GPT-6 Astra |
| `--no-escalate` | Skip retries of rows the first pass cannot source |
| `estimate --compare` | Compare both providers on identical assumed workloads |

| Output | Detail |
| --- | --- |
| `<bom>_cbom.csv` | Saved CBOM folder (home before setup); BOM + vendor + pricing ([docs/FORMATS.md](docs/FORMATS.md)) |
| `quote_cache.json` | Reused quotes (personal profile, outside Git) |
| `run_log.jsonl` | Actual vs estimated cost per pass, for calibration (personal profile, outside Git) |
| `supplier_candidates.json` | Discovered suppliers, screening, trials, status (personal profile, outside Git) |
| Summary | Rows by status, cache reuse, parts total, tokens and cost per model |
| Exit code | `0` ok · `1` some rows errored · `2` bad input or credentials |

In Claude Code: `/source-bom` and `/find-suppliers` (each linked from `skill/<name>/` into `~/.claude/skills/`).

## Personal memory

On first skill use, initialize folders and review suppliers and discovery sources using the [startup sequence](docs/PERSONAL_SETUP.md); `~/sourcing/` is suggested. Existing folder choices are reused. Bare input/output names use saved folders; `./file.csv` or absolute paths override them. Without skill setup, CLI folders default to home.

| Local file | Purpose |
| --- | --- |
| `memory.json` | Folder choices, user-specific facts/preferences, and acknowledged source revision; shared rules belong in skills/code or `AGENTS.md` |
| `run_log.jsonl` | API-pass cost, quote outcomes, supplier reliability; automatically used by estimates and `sourcing learn` |
| `events.jsonl` | Command outcomes, including failures before API work; summarized by `sourcing memory` |
| `quote_cache.json`, `supplier_candidates.json` | Personal quote reuse and supplier discovery state |

All live under `~/.local/share/sourcing-agent/`, outside Git. OS accounts have separate memory. Set `SOURCING_AGENT_HOME` to choose another private profile (including for people sharing an OS account). Skills review history before paid work and results afterward; reusable fixes belong in shared code/skills, while personal notes must describe the user's choices or environment. Code records safe outcomes automatically, excluding raw exception text and credentials.

Existing checkout logs are not automatically imported: copy only your own local data into an empty profile, keeping the originals. `sourcing memory` shows the active profile and setup status; it makes no API calls. The approved supplier list remains in the checkout.

## Estimated cost

```
sourcing run ──▶ claude-sonnet-5 (all new rows) ──▶ not_found / error rows ──▶ claude-opus-5-5 retry (once)
```

`sourcing estimate <bom>` prices a run before it starts. List prices, effort `medium`, assumed token sizes (not yet measured live).

| Factor | Effect |
| --- | --- |
| New rows | Main driver; cached rows cost $0 |
| Suppliers per category | 1 search ($0.01) per supplier per row, up to 6 |
| Page fetches, turns | Low case none / 1; high case 1 per supplier / 6 |
| Mode | Batch halves token cost (search fees unchanged) |
| Opus retries | Only failed rows; ~1.4–1.7× Sonnet per row |

Model selection applies to `run` and `estimate`; supplier discovery and trials retain Anthropic. OpenAI uses domain-filtered web search, strict quote submissions, and the existing quote validator. Its hosted web tool controls page sizes; the Anthropic 15K page cap does not apply. OpenAI batch sourcing is not implemented.

| Provider | Models | Run modes |
| --- | --- | --- |
| Anthropic | `claude-sonnet-5`, `claude-opus-5-5` | Batch, live |
| OpenAI | `gpt-6-sol`, `gpt-6-luna`, `gpt-6-astra` | Live |

`estimate --compare` shows per-meter prices and first-pass BOM costs on shared assumptions. Actual token counts, search counts, and quote coverage can differ by model. `sourcing learn` compares measured costs and quote coverage after runs are logged. Cache reuse is shared across providers; use `--max-age 0` for a fresh comparison.

Rates checked 2026-09-26: [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI pricing](https://developers.openai.com/api/docs/pricing). Estimates use standard short-context rates, excluding taxes and regional premiums. OpenAI integration follows [Responses web search](https://developers.openai.com/api/docs/guides/tools-web-search) and [cache metering](https://developers.openai.com/api/docs/guides/prompt-caching).

### Calibration

```
sourcing run ──▶ run_log.jsonl (actual vs estimate, per pass) ──▶ sourcing estimate refits ──▶ calibrated figure
```

| Logged clean passes | Fit |
| --- | --- |
| 0 | None; ranges only |
| 1–2 | Ratio: `actual ≈ β1 × estimate midpoint` |
| 3+ | Least squares: `actual ≈ β0 + β1 × estimate midpoint`, with R² and typical error |

Supplier learning logs each row’s supplier name/domains, search status/reason, and price-rule win. Valid quotes (even losing quotes) reset failure streaks. Errors, unchecked rows, and samples under five rows break a first-pass streak. Cache hits, duplicate pass IDs, retries, trials, and older totals-only logs cannot add failing runs. Drops require user selection; keep at least one supplier per category.

Profiles and run-estimate calibration are pooled only within the selected provider. Passes with errored rows are logged but not fitted. Estimates are recomputed from each pass's conversation shape, so changing the token assumptions keeps old runs usable.

## Code layout

| Area | Responsibility |
| --- | --- |
| `cli.py` | Command composition, errors and safe command events |
| `core/` | Providers, bounded execution, pricing, configuration and atomic storage |
| `personal/` | Per-user folder choices, lessons and outcome history |
| `learning/` | Cost calibration, token profiles and supplier reliability |
| `cbom/` | Planning → pass execution → orchestration → export; separate service and reports |
| `suppliers/` | Discovery, outside verification, screening, trials and approval |

[Architecture and failure boundaries](docs/ARCHITECTURE.md) explain the dependency rules and persistence behavior. Completed sourcing passes are logged before retries/export; fully cached runs need no API client. Failed CSV/JSON serialization preserves the previous file.

| Status | State |
| --- | --- |
| CBOM generation | ✅ first live run: Arctos 76 rows, 56 sourced, $37.36 API (live) |
| Learning | ✅ conversation logging, settings tags, learned profile, `sourcing learn` (tested offline) |
| Supplier discovery | ✅ tested offline; no live scout yet |
