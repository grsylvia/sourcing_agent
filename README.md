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
sourcing run examples/bom.csv --out cbom.csv
```

| Flag | Effect |
| --- | --- |
| *(default)* | Batch API: 50% off tokens; usually under 1 h, max 24 h |
| `--live` | Full price, results in minutes |
| `--max-age DAYS` | Reuse cached quotes up to this age (default 7; `0` = re-source all) |

| Output | Detail |
| --- | --- |
| `cbom.csv` | BOM + vendor + pricing ([docs/FORMATS.md](docs/FORMATS.md)) |
| `quote_cache.json` | Reused quotes (local, gitignored) |
| Summary | Rows by status, cache reuse, parts total, tokens, estimated API cost |
| Exit code | `0` ok · `1` some rows errored · `2` bad input or credentials |

In Claude Code: `/source-bom` (linked from `skill/source-bom/` into `~/.claude/skills/`).

## Estimated cost

Sonnet 5 · medium, list prices, first run (cache empty); not yet measured live.

| Mode | Per row | Example (6 rows) | Arctos (75 rows) |
| --- | --- | --- | --- |
| **Batch (default)** | $0.08–0.29 | $0.50–1.77 | $6–22 |
| Live | $0.13–0.53 | $0.77–3.18 | $10–40 |

Rows reused from the cache cost $0. Opus 5.5 is ~1.8× these figures.

## Status

| Part | State |
| --- | --- |
| Project setup | ✅ |
| Formats (BOM, suppliers, CBOM) | ✅ [docs/FORMATS.md](docs/FORMATS.md) |
| Category worker | ✅ `src/sourcing_agent/worker.py` |
| Orchestrator | ✅ `src/sourcing_agent/orchestrator.py` |
| CLI | ✅ `src/sourcing_agent/cli.py` |
| Claude Code skill | ✅ `skill/source-bom/SKILL.md` |
| Live run | ⏳ needs `ANTHROPIC_API_KEY` |
