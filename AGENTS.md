# Sourcing Agent

Turn a validated BOM into a costed BOM using approved suppliers. Preserve evidence, minimize repeat API work, and let the user control supplier changes.

## Working rules

- Make all repository changes on `cost-optimizations`; do not edit `main` or create branches without the user's instruction.
- Complete authorized work within scope. Ask only for missing decisions or authorization that the task actually requires.
- Preserve existing personal data and uncommitted work. Never commit credentials, `.env`, BOMs, or personal memory.
- Keep Markdown concise; use tables for mappings and decisions.
- Comment generated code/configuration with one short comment immediately above each statement or setting.
- Run relevant offline tests with isolated personal storage. No paid API runs unless authorized.

## First principles

| Principle | Implementation rule |
| --- | --- |
| Code owns decisions | Models find quotes; validation and lowest-total selection stay deterministic |
| Evidence before conclusions | A missing quote, worker error, unchecked supplier, and price loss are different observations |
| One plan | Estimates and execution share category batching and cache selection in `cbom/planning.py` |
| Small boundaries | CLI parses/renders; services coordinate files and API lifecycle; pipelines operate on supplied data |
| Paid work leaves evidence | Persist each completed sourcing pass before retry/export; rendering must not write learning records |
| Private learning | Each user's local history informs estimates and review; it does not train model weights or authorize changes |
| Safe local writes | Replace complete snapshots atomically; append learning/events under a writer lock |
| Honest unknowns | Interrupted requests and incomplete outside verification must remain unknown, not inferred failures |

## Architecture

| Area | Owns | Dependencies |
| --- | --- | --- |
| `core/` | Provider adapters, job runner, pricing, config, paths, storage primitives | No other project package |
| `learning/` | Pass records, token profiles, calibration, supplier failure history | `core` |
| `personal/` | Folder preferences, explicit lessons, safe command outcomes | `core` |
| `cbom/` | BOM/quote contracts, planning, execution, orchestration, exports, estimates | `core`, `learning`; command adapter also uses `personal` |
| `suppliers/` | Discovery, outside evidence, screening, registry, trials | `core`, `learning`, `cbom`; command adapter also uses `personal` |
| `cli.py` | Command composition, errors, exit status, command event recording | Command adapters |

Within `cbom/`: `commands → service → pipeline → execution → planning`; `estimate → planning`; `report` renders facts. Domain and planning modules must not import CLI commands or services. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Behavioral contracts

| Topic | Contract |
| --- | --- |
| Inputs/outputs | CSV or `.xlsx` BOM → CSV CBOM; preserve [docs/FORMATS.md](docs/FORMATS.md) and existing CLI commands |
| Price | Lowest order total at BOM quantity, including packs, MOQ and price breaks; shipping excluded |
| Domain lock | Production workers use approved domains; discovery alone searches the open web |
| Workload | Category batches of two rows; live concurrency is bounded by the runner |
| Providers | Anthropic Messages: batch default or live; OpenAI Responses: live only; no silent provider/mode fallback |
| Models | Defaults and selectable models live in `core/providers.py`; prices in `core/pricing.py`; do not duplicate these policies |
| Retry | Failed/not-found rows get at most one stronger-model pass within the selected provider; `--no-escalate` disables it |
| Cache | Valid quotes reused up to seven days by default; no credentials/client needed when every row is cached; never count reuse as supplier failure evidence |
| Learning | Freeze pre-run estimates; persist completed passes with settings, usage, conversations and supplier outcomes; isolate provider calibration |
| Supplier drops | User selection only after ≥3 consecutive first passes with ≥5 checked rows each and no valid quotes; any valid quote resets; errors, unchecked rows and smaller samples break the streak; retain ≥1 supplier/category |
| Discovery | Use [docs/SUPPLIER_SOURCES.md](docs/SUPPLIER_SOURCES.md); retain dated outside evidence and concerns; missing reviews mean unknown |
| Approval | Screen → trial on real rows → user approval; skipping a trial requires explicit user authorization |
| Run authorization | Skills show estimates and confirm paid work; reuse choices and authorization already supplied by the user |

## Personal setup and memory

- Follow [docs/PERSONAL_SETUP.md](docs/PERSONAL_SETUP.md): read memory, initialize missing folders, and review the current suppliers and discovery sources with the user before paid work.
- Copy `templates/bom_template.xlsx` into the chosen template folder only if absent. Bare filenames use saved folders; explicit paths override them.
- Personal files live in `~/.local/share/sourcing-agent/` or `SOURCING_AGENT_HOME`, separate from Git and shared across checkouts for that user.
- Review `sourcing learn` and saved outcomes before paid work. Personal memory holds only user-specific choices and local facts; shared rules and reusable fixes belong in skills/code, branch policy in AGENTS.md. Code records safe command outcomes separately.
- Keep credentials and raw exception text out of command events. Import historical files only when identified as that user's own data.
- OS accounts are separate by default; people sharing an account need separate `SOURCING_AGENT_HOME` directories.

## Validation

Run `.venv/bin/python -m unittest discover -s tests -v` with `SOURCING_AGENT_HOME` set to a temporary directory. Test boundary behavior: prices, supplier restrictions, retries, provider attribution, cache-only runs, failure persistence, personal isolation and file formats. Use mocked API boundaries; keep the real serialization test offline.
