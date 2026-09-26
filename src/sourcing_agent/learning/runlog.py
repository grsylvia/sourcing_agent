"""Run log (run_log.jsonl): one record per API pass (shape, usage, estimate, actual cost); the training data for learning."""

# Record dates.
import datetime
# Log format.
import json
# Usage to a dict.
from dataclasses import asdict
# File paths.
from pathlib import Path

# Usage totals.
from ..core.agent import Usage
# Cost model.
from ..core.pricing import Range, estimate_cost


def append_run(path: Path, record: dict) -> None:
    """Append one pass record to the run log."""
    # One JSON object per line.
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_runs(path: Path) -> list[dict]:
    """Read every pass record, skipping unreadable lines."""
    # No log yet.
    if not path.exists():
        return []
    # Parsed records.
    records = []
    # One record per non-blank line.
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A damaged line is ignored.
            continue
    # All readable records.
    return records


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
) -> float | None:
    """Append one pass with its estimate and actual cost; return the actual cost, or None if it made no requests."""
    # Passes that made no requests teach nothing.
    if not usage.requests:
        return None
    # Actual list-price cost of the pass.
    actual = estimate_cost(usage, model, batch)
    # One log record per pass.
    append_run(path, {
        "date": datetime.date.today().isoformat(),
        "bom": source,
        "pass": name,
        "model": model,
        "batch": batch,
        "rows": sum(n for n, _ in shape),
        "shape": shape,
        "error_rows": error_rows,
        "estimate_low": round(estimate.low, 4),
        "estimate_high": round(estimate.high, 4),
        "actual_cost": round(actual, 4),
        "usage": asdict(usage),
    })
    # Actual cost for the caller's report.
    return actual
