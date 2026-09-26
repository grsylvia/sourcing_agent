"""Project files the commands read and write."""

# File paths.
from pathlib import Path

# Project root folder (src/sourcing_agent/core/paths.py is three levels down).
PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Approved supplier list.
DEFAULT_SUPPLIERS = PROJECT_ROOT / "suppliers.toml"
# Quote cache.
CACHE_PATH = PROJECT_ROOT / "quote_cache.json"
# Actual-vs-estimated cost log.
RUN_LOG_PATH = PROJECT_ROOT / "run_log.jsonl"
# Discovered-supplier registry.
REGISTRY_PATH = PROJECT_ROOT / "supplier_candidates.json"
