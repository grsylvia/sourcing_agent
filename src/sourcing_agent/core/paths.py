"""Project files the commands read and write."""

# File paths.
from pathlib import Path
# Optional isolated personal profile.
import os

# Project root folder (src/sourcing_agent/core/paths.py is three levels down).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Approved supplier list.
DEFAULT_SUPPLIERS = PROJECT_ROOT / "suppliers.toml"
# Personal preferences persist across checkouts.
USER_DATA = Path(os.environ.get("SOURCING_AGENT_HOME", "~/.local/share/sourcing-agent")).expanduser().resolve()
# User-specific preferences only.
MEMORY_PATH = USER_DATA / "memory.json"
# Safe command outcomes are recorded separately from preferences.
EVENTS_PATH = USER_DATA / "events.jsonl"
# Quote cache.
CACHE_PATH = PROJECT_ROOT / "quote_cache.json"
# Actual-vs-estimated cost log.
RUN_LOG_PATH = PROJECT_ROOT / "run_log.jsonl"
# Discovered-supplier registry.
REGISTRY_PATH = PROJECT_ROOT / "supplier_candidates.json"

# Maintained discovery sources and outside-verification procedure.
SUPPLIER_SOURCES_PATH = PROJECT_ROOT / "docs" / "SUPPLIER_SOURCES.md"
