"""Project files the commands read and write."""

# File paths.
from pathlib import Path
# Optional location for an isolated personal profile.
import os

# Project root folder (src/sourcing_agent/core/paths.py is three levels down).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Approved supplier list.
DEFAULT_SUPPLIERS = PROJECT_ROOT / "suppliers.toml"
# Personal data stays outside checkouts and is separated by OS account.
USER_DATA = Path(os.environ.get("SOURCING_AGENT_HOME", "~/.local/share/sourcing-agent")).expanduser().resolve()
# Saved folder choices and user-authored lessons.
MEMORY_PATH = USER_DATA / "memory.json"
# Command outcomes supplement API-pass learning with early failures.
EVENTS_PATH = USER_DATA / "events.jsonl"
# Quote cache.
CACHE_PATH = USER_DATA / "quote_cache.json"
# Actual-vs-estimated cost log.
RUN_LOG_PATH = USER_DATA / "run_log.jsonl"
# Discovered-supplier registry.
REGISTRY_PATH = USER_DATA / "supplier_candidates.json"

# Maintained discovery sources and outside-verification procedure.
SUPPLIER_SOURCES_PATH = PROJECT_ROOT / "docs" / "SUPPLIER_SOURCES.md"
