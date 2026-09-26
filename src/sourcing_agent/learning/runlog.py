"""Run log (run_log.jsonl): one record per API pass (settings, shape, usage, estimate, actual cost, outcomes) with one entry per conversation; the training data for learning."""

# Record dates.
import datetime
# Settings IDs.
import hashlib
# Log format.
import json
# Unique pass identity for deduplicating learning evidence.
from uuid import uuid4
# Usage to a dict.
from dataclasses import asdict
# File paths.
from pathlib import Path

# Shared append-only persistence.
from ..core.storage import append_jsonl, read_jsonl
# Job and usage totals.
from ..core.agent import Job, Usage
# Cost model.
from ..core.pricing import Range, estimate_cost
# Provider identity for cost attribution.
from ..core.providers import provider_of


def settings_id(settings: dict) -> str:
    """Short stable ID for a settings dict, ignoring model and mode (reported separately)."""
    # Settings that define the worker's behavior.
    core = {k: v for k, v in settings.items() if k not in ("model", "batch")}
    # First 8 hex digits of the hash.
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:8]


def conversation_record(job: Job, outcome: str, quoted_rows: int) -> dict:
    """One conversation's facts, usage, and outcome for the log."""
    # Job facts (category, rows, suppliers) plus what happened.
    return {"label": job.label, **job.meta, "turns": job.turns, "outcome": outcome, "quoted_rows": quoted_rows, "usage": asdict(job.usage)}


def append_run(path: Path, record: dict) -> None:
    """Append one pass record without interleaving concurrent writers."""
    # Keep completed-pass evidence append-only.
    append_jsonl(path, record)


def load_runs(path: Path) -> list[dict]:
    """Read intact pass records, preserving older record formats."""
    # Shared parsing skips damaged and non-object lines.
    return read_jsonl(path)


def record_pass(
    path: Path,
    source: str,
    name: str,
    model: str,
    batch: bool,
    shape: list[tuple[int, int]],
    error_rows: int,
    usage: Usage,
    estimate: Range,
    settings: dict | None = None,
    conversations: list[dict] | None = None,
) -> float | None:
    """Append one pass with its estimate, actual cost, settings, and conversations; return the actual cost, or None if it made no requests."""
    # Passes that made no requests teach nothing.
    if not usage.requests:
        return None
    # Actual list-price cost of the pass.
    actual = estimate_cost(usage, model, batch)
    # Conversations logged for this pass.
    conversations = conversations or []
    # One log record per pass.
    append_run(path, {
        "run_id": str(uuid4()),
        "date": datetime.date.today().isoformat(),
        "bom": source,
        "pass": name,
        "model": model,
        "provider": provider_of(model),
        "batch": batch,
        "rows": sum(n for n, _ in shape),
        "shape": shape,
        "error_rows": error_rows,
        "estimate_low": round(estimate.low, 4),
        "estimate_high": round(estimate.high, 4),
        "actual_cost": round(actual, 4),
        "usage": asdict(usage),
        "settings": settings or {},
        "settings_id": settings_id(settings) if settings else "",
        "quoted_rows": sum(c["quoted_rows"] for c in conversations) if conversations else None,
        "conversations": conversations,
    })
    # Actual cost for the caller's report.
    return actual
