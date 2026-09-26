# Sourcing Agent

Takes a BOM, sources each category from approved suppliers, and returns a CBOM (BOM + vendor + pricing).

# Decisions

| Topic | Decision |
| --- | --- |
| Runtime | Local Python on Anthropic Messages or OpenAI Responses (`anthropic`, `openai` SDKs) |
| Code layout | `core/` shared engine, `learning/` run-by-run learning, `cbom/` CBOM generation, `suppliers/` supplier management; imports flow `suppliers → cbom → learning → core` only |
| Learning goal | `learning/` exists for token minimization and cost optimization; it learns from every logged API pass |
| Learning data | Each pass logs its settings (ID + prompt hash) and one record per conversation (usage by meter, turns, rows quoted, per-row supplier identities/domains, search outcomes/reasons, wins); older totals-only records stay usable |
| Token profile | Estimator uses a profile learned per model from ≥5 conversations (p25–p90), else pooled within provider, else the assumed constants |
| Fan-out | Code splits the BOM by category; one worker per category, run in parallel |
| Supplier lock | Worker web search/fetch restricted to approved supplier domains (`allowed_domains`) |
| Vendor selection | Workers return quotes; code picks the lowest price |
| Interface | CLI, run by Claude through a Claude Code skill |
| Price rule | Lowest total at BOM qty (price breaks, pack size, MOQ); shipping excluded |
| Formats | BOM CSV or .xlsx (template in `templates/`), `suppliers.toml`, CBOM CSV ([docs/FORMATS.md](docs/FORMATS.md)) |
| Model | Anthropic default `claude-sonnet-5`; OpenAI default `gpt-6-sol` (Luna/Astra selectable); effort `medium`; no refusal fallback |
| Escalation | Rows left `not_found` / `error` retried once on selected provider: Opus 5.5 or GPT-6 Astra by default; `--escalation-model` overrides; `--no-escalate` skips |
| Token use | 2 rows per worker, 15K-token page cap, prompt caching, usage + cost per run |
| Shared cache prefix | Tools + system + category instructions byte-identical per category, cache breakpoint before the per-batch rows |
| Supplier pruning | `sourcing suppliers <cbom>…` uses learning history: ≥3 consecutive first-pass runs with ≥5 explicitly checked rows each and no valid quotes; errors/unchecked rows or smaller samples break the streak, any valid quote resets it; zero wins alone never qualifies; user picks drops; retain ≥1 supplier per category |
| Supplier discovery | Separate ability (`/find-suppliers`, `sourcing discover`): one open-web scout per category with unsourced rows; loads `docs/SUPPLIER_SOURCES.md`, saves dated outside evidence and flags incomplete checks/concerns; proposals only ([docs/DISCOVERY.md](docs/DISCOVERY.md)) |
| Supplier verification | Free screen (own-domain priced evidence, HTTPS, login-free prices, ships to US, RDAP age) → trial on real CBOM rows → user approval (`sourcing candidates --approve`) |
| Run mode | Anthropic Batch API by default (50% off); `--live` for fast runs; OpenAI requires `--live` |
| Run gate | Skill asks provider/model with cost comparison, supported mode, retry model, quote freshness, output path (with estimates), then a final Run / Cancel confirmation |
| Cost estimate | `sourcing estimate --compare` compares providers on shared assumptions; selected-provider estimate before each run: rows, cache hits, suppliers per category, mode, model; no API calls |
| Cost calibration | `learning/`: each sourcing or trial pass logs actual vs estimated cost to `run_log.jsonl`; `regression.py` fits `actual = β0 + β1 × estimate` (least squares; ratio fit under 3 clean passes; errored passes excluded) |
| Quote reuse | `quote_cache.json`, rows reused up to `--max-age` days (default 7); keyed by part + suppliers, not quantity |

# Project resources

| Resource | Use |
| --- | --- |
| [Claude API docs](https://docs.claude.com/) | Messages API, tool use, web search/fetch |

# Project guidance

- Work step by step and only move forward on the user's command. When the user shares a goal or context, acknowledge it and wait.
- Code generation is user-driven. Keep changes within the user's instructions; do not add features or expand scope.
- The agent is responsible for writing and modifying the project code.
- Comment generated code and configuration: one short comment immediately above each statement or setting, at most one sentence on one line.
- Markdown documentation must be concise, simple, and light on text. Prefer tables and visualizations over paragraphs.
- All Windows exports must go to the user's OneDrive Documents folder.
- Work on and push to `main`. Ask before creating any new branch.
- Never commit API keys or `.env` files.
