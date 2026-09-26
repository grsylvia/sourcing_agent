# Sourcing Agent

Takes a BOM, sources each category from approved suppliers, and returns a CBOM (BOM + vendor + pricing).

# Decisions

| Topic | Decision |
| --- | --- |
| Runtime | Local Python on the Claude Messages API (`anthropic` SDK) |
| Fan-out | Code splits the BOM by category; one worker per category, run in parallel |
| Supplier lock | Worker web search/fetch restricted to approved supplier domains (`allowed_domains`) |
| Vendor selection | Workers return quotes; code picks the lowest price |
| Interface | CLI, run by Claude through a Claude Code skill |
| Price rule | Lowest total at BOM qty (price breaks, pack size, MOQ); shipping excluded |
| Formats | BOM CSV, `suppliers.toml`, CBOM CSV ([docs/FORMATS.md](docs/FORMATS.md)) |
| Model | `claude-sonnet-5`, effort `medium`; no refusal fallback (not on Batch API) |
| Token use | 2 rows per worker, 15K-token page cap, prompt caching, usage + cost per run |
| Run mode | Batch API by default (50% off); `--live` for full-price fast runs |
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
