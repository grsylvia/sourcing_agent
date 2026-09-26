# Sourcing Agent

BOM in → CBOM out (BOM + vendor + pricing), sourced from approved suppliers.

```
BOM ──▶ split by category ──▶ worker per category (parallel) ──▶ lowest price ──▶ CBOM
                                 └─ web search: approved suppliers only
```

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
| Summary | Rows by status, cache reuse, parts total, tokens and cost per model |
| Exit code | `0` ok · `1` some rows errored · `2` bad input or credentials |

In Claude Code: `/source-bom` (linked from `skill/source-bom/` into `~/.claude/skills/`).

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

## Status

| Part | State |
| --- | --- |
| Project setup | ✅ |
| Formats (BOM, suppliers, CBOM) | ✅ [docs/FORMATS.md](docs/FORMATS.md) |
| Category worker | ✅ `src/sourcing_agent/worker.py` |
| Orchestrator | ✅ `src/sourcing_agent/orchestrator.py` |
| CLI | ✅ `src/sourcing_agent/cli.py` |
| Claude Code skill | ✅ `skill/source-bom/SKILL.md` |
| Excel BOM input + template | ✅ `templates/bom_template.xlsx` |
| Cost estimate + Opus 5.5 escalation | ✅ `src/sourcing_agent/estimate.py` |
| Live run | ⏳ needs `ANTHROPIC_API_KEY` |
