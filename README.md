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
| BOM → CBOM | `/source-bom` | `estimate`, `run`, `suppliers` |
| Find and verify suppliers | `/find-suppliers` | `discover`, `trial`, `candidates` ([docs/DISCOVERY.md](docs/DISCOVERY.md)) |

## Setup

| Step | Command |
| --- | --- |
| Create venv | `python3 -m venv .venv` |
| Activate | `source .venv/bin/activate` |
| Install | `pip install -e .` |
| API key | `export ANTHROPIC_API_KEY=...` |

## Usage

```
sourcing estimate my_bom.xlsx            # price batch vs live first (no API calls)
sourcing run my_bom.xlsx --out cbom.csv  # batch (default)
sourcing run my_bom.xlsx --live          # live
sourcing suppliers cbom.csv              # supplier win rates; flags suppliers that never win
sourcing discover cbom.csv --estimate    # plan + cost of scouting categories with unsourced rows
sourcing discover cbom.csv               # scout, screen, save candidates
sourcing trial vxb.com --cbom cbom.csv   # quote sample rows on one candidate only
sourcing candidates --approve vxb.com    # add a trialed candidate to suppliers.toml
sourcing learn                           # what logged runs teach: spend by meter/category/settings, learned token profile, next steps
```

| BOM input | Detail |
| --- | --- |
| CSV | [`examples/bom.csv`](examples/bom.csv) |
| Excel | Copy [`templates/bom_template.xlsx`](templates/bom_template.xlsx), fill the `BOM` sheet |

| Flag | Effect |
| --- | --- |
| *(default)* | Batch API: 50% off tokens; usually under 1 h, max 24 h |
| `--live` | Full price, results in minutes |
| `--max-age DAYS` | Reuse cached quotes up to this age (default 7; `0` = re-source all) |
| `--no-escalate` | Skip the Opus 5.5 retry of rows Sonnet cannot source |

| Output | Detail |
| --- | --- |
| `cbom.csv` | BOM + vendor + pricing ([docs/FORMATS.md](docs/FORMATS.md)) |
| `quote_cache.json` | Reused quotes (local, gitignored) |
| `run_log.jsonl` | Actual vs estimated cost per pass, for calibration (local, gitignored) |
| `supplier_candidates.json` | Discovered suppliers, screening, trials, status (local, gitignored) |
| Summary | Rows by status, cache reuse, parts total, tokens and cost per model |
| Exit code | `0` ok · `1` some rows errored · `2` bad input or credentials |

In Claude Code: `/source-bom` and `/find-suppliers` (each linked from `skill/<name>/` into `~/.claude/skills/`).

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

Example BOM (6 rows, cache empty):

| Mode | Sonnet 5 pass | Opus 5.5 retry / row | Range (no retries → all retried) |
| --- | --- | --- | --- |
| **Batch (default)** | $0.46–1.28 | $0.11–0.34 | $0.46–3.33 |
| Live | $0.66–2.20 | $0.17–0.62 | $0.66–5.94 |

Arctos-size BOM (75 rows), Sonnet pass: ~$6–16 batch, ~$8–28 live.

### Calibration

```
sourcing run ──▶ run_log.jsonl (actual vs estimate, per pass) ──▶ sourcing estimate refits ──▶ calibrated figure
```

| Logged clean passes | Fit |
| --- | --- |
| 0 | None; ranges only |
| 1–2 | Ratio: `actual ≈ β1 × estimate midpoint` |
| 3+ | Least squares: `actual ≈ β0 + β1 × estimate midpoint`, with R² and typical error |

Passes with errored rows are logged but not fitted. Estimates are recomputed from each pass's conversation shape, so changing the token assumptions keeps old runs usable.

## Code layout

```
src/sourcing_agent/
├── cli.py          thin entry point: registers both command sets, maps errors to exit codes
├── core/           shared engine (imports nothing else in the package)
├── learning/       run-by-run learning for token and cost minimization (imports core/ only)
├── cbom/           CBOM generation (imports core/, learning/)
└── suppliers/      supplier management (imports core/, learning/, cbom/)
```

| Package | Module | Role |
| --- | --- | --- |
| `core` | `agent.py` | Agent loop: `Job`, `Usage`, request params, submit-tool handling, live conversation |
| | `runner.py` | Runs jobs live (4 at a time) or through the Batch API |
| | `config.py` · `paths.py` · `errors.py` · `web.py` | `suppliers.toml`, project files, errors, domain helpers |
| | `pricing.py` | API cost model, cost ranges, shared token-size assumptions |
| `learning` | `runlog.py` | Every API pass and its conversations: settings tag, usage by meter, turns, rows quoted, estimate, actual cost |
| | `profile.py` | Token profile learned from conversations (p25–p90), per model, replacing guessed constants at ≥5 conversations |
| | `regression.py` | Estimated-vs-actual least-squares fit |
| | `calibration.py` | Corrects estimates with the fit (estimator injected by the worker); per-pass comparison |
| | `report.py` · `commands.py` | `sourcing learn`: spend by settings, meter, category, conversation; recommendations |
| `cbom` | `bom.py` | BOM (CSV/.xlsx) in, CBOM out, CBOMs read back |
| | `quotes.py` | Quote records and the lowest-total price rule |
| | `worker.py` | Sourcing worker (approved domains only) |
| | `pipeline.py` · `cache.py` | Batches, cache reuse, Opus 5.5 escalation, CBOM rows |
| | `estimate.py` · `commands.py` | Sourcing estimator (calibrated by `learning`); `run`, `estimate` |
| `suppliers` | `wins.py` | Win rates per supplier and category |
| | `scout.py` · `screen.py` | Open-web scout; free screening (evidence, HTTPS, RDAP age) |
| | `trial.py` · `registry.py` | Trial on real rows; candidate registry and approvals |
| | `commands.py` | `suppliers`, `discover`, `trial`, `candidates` |

| Status | State |
| --- | --- |
| CBOM generation | ✅ first live run: Arctos 76 rows, 56 sourced, $37.36 API (live) |
| Learning | ✅ conversation logging, settings tags, learned profile, `sourcing learn` (tested offline) |
| Supplier discovery | ✅ tested offline; no live scout yet |
