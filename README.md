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

## Status

| Part | State |
| --- | --- |
| Project setup | ✅ |
| Formats (BOM, suppliers, CBOM) | ⏳ |
| Category worker | ⏳ |
| Orchestrator | ⏳ |
| CLI | ⏳ |
| Claude Code skill | ⏳ |
