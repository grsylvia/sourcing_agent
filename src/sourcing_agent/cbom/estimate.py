"""CBOM cost estimate: prices sourcing passes before a run, calibrates on logged actual costs, and logs each pass."""

# Log dates.
import datetime
# Sum usage fields and record usage.
from dataclasses import asdict, dataclass, fields
# File paths.
from pathlib import Path

# Models, turn cap, and usage totals.
from ..core.agent import ESCALATION_MODEL, MAX_TURNS, MODEL, Usage
# Run log and fitted line.
from ..core.calibration import Fit, append_run, fit_line, load_runs
# Supplier list and per-category suppliers.
from ..core.config import category_suppliers, load_suppliers
# Cost model and shared token assumptions.
from ..core.pricing import OUTPUT_PER_ROW, PROMPT_TOKENS, SEARCH_TOKENS, Range, estimate_cost, money
# BOM reader.
from .bom import load_bom
# Quote cache reader.
from .cache import load_cache
# Conversation shapes and the cache split shared with real runs.
from .pipeline import pass_shape, split_cached
# Sourcing worker limits.
from .worker import MAX_PAGE_TOKENS, SEARCHES_PER_PART

# Page tokens per web fetch in the low case (the high case uses the MAX_PAGE_TOKENS cap).
FETCH_TOKENS_LOW = 5_000
# Times gathered context is re-read from the prompt cache, low and high (calibration corrects the gap).
REREADS = (1, 3)


# Pre-run estimate for a BOM.
@dataclass
class Estimate:
    # BOM rows.
    rows: int
    # Rows answered free from the quote cache.
    cached: int
    # Rows that need sourcing.
    to_source: int
    # Worker conversations for those rows.
    conversations: int
    # (category, rows to source, approved suppliers) per category.
    categories: list[tuple[str, int, int]]
    # Cost range keyed by (batch, model); escalation ranges assume every row is retried.
    costs: dict[tuple[bool, str], Range]
    # Line fitted to logged actual costs, or None before any clean run.
    calibration: Fit | None


def conversation_usage(n_rows: int, n_suppliers: int, high: bool) -> Usage:
    """Assumed usage of one sourcing conversation, low or high case."""
    # Low: one search per supplier; high: the per-row search cap.
    searches = n_rows * (SEARCHES_PER_PART if high else min(n_suppliers, SEARCHES_PER_PART))
    # Low: search results suffice; high: one page fetch per supplier.
    fetches = n_rows * n_suppliers if high else 0
    # Tokens gathered from searches and pages.
    context = searches * SEARCH_TOKENS + fetches * (MAX_PAGE_TOKENS if high else FETCH_TOKENS_LOW)
    # Low: one request; high: the turn cap.
    turns = MAX_TURNS if high else 1
    # Usage for the conversation.
    return Usage(
        requests=turns,
        input_tokens=context,
        cache_write_tokens=PROMPT_TOKENS,
        cache_read_tokens=PROMPT_TOKENS * (turns - 1) + context * REREADS[high],
        output_tokens=n_rows * OUTPUT_PER_ROW[high],
        web_searches=searches,
        web_fetches=fetches,
    )


def total_usage(shape: list[tuple[int, int]], high: bool) -> Usage:
    """Sum assumed usage over sourcing conversations given as (rows, suppliers)."""
    # Running total.
    total = Usage()
    # Add each conversation.
    for n_rows, n_suppliers in shape:
        u = conversation_usage(n_rows, n_suppliers, high)
        for f in fields(Usage):
            setattr(total, f.name, getattr(total, f.name) + getattr(u, f.name))
    # Totals.
    return total


def shape_range(shape: list[tuple[int, int]], model: str, batch: bool) -> Range:
    """Assumption-based cost range for a sourcing pass on one model in one mode."""
    # Low and high costs.
    return Range(estimate_cost(total_usage(shape, False), model, batch), estimate_cost(total_usage(shape, True), model, batch))


def calibrate(log_path: Path) -> Fit | None:
    """Fit logged actual costs against today's assumption-based estimate midpoints."""
    # Passes that finished without errored rows (errored rows cut the cost short).
    runs = [r for r in load_runs(log_path) if not r["error_rows"]]
    # Estimate midpoint (current assumptions) and actual cost per pass.
    points = [(shape_range([tuple(c) for c in r["shape"]], r["model"], r["batch"]).mid, r["actual_cost"]) for r in runs]
    # Least-squares line, or None with no usable passes.
    return fit_line(points)


def estimate_bom(bom_path: Path, suppliers_path: Path, cache_path: Path, max_age_days: int, log_path: Path) -> Estimate:
    """Estimate the cost of sourcing a BOM in each mode, on each model."""
    # Approved suppliers and categories.
    config = load_suppliers(suppliers_path)
    # Checked BOM rows.
    rows = load_bom(bom_path, config["categories"])
    # Rows the cache already answers.
    cached, todo, _ = split_cached(rows, config, load_cache(cache_path), max_age_days, datetime.date.today())
    # Worker conversations a run would start.
    shape = pass_shape(todo, config["suppliers"])
    # Cost per mode and model.
    costs = {(batch, model): shape_range(shape, model, batch) for batch in (True, False) for model in (MODEL, ESCALATION_MODEL)}
    # Rows to source per category, in BOM order.
    counts: dict[str, int] = {}
    for r in todo:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    # Category breakdown with supplier counts.
    categories = [(c, n, len(category_suppliers(c, config["suppliers"]))) for c, n in counts.items()]
    # The estimate.
    return Estimate(len(rows), len(cached), len(todo), len(shape), categories, costs, calibrate(log_path))


def log_pass(log_path: Path, name: str, model: str, shape: list[tuple[int, int]], error_rows: int, usage: Usage, batch: bool, source: str, fit: Fit | None) -> str | None:
    """Append one sourcing pass to the run log; return its actual-vs-estimate line, or None if it made no requests."""
    # Skip passes that made no requests.
    if not usage.requests:
        return None
    # Assumption-based range for exactly these rows.
    est = shape_range(shape, model, batch)
    # Actual list-price cost of the pass.
    actual = estimate_cost(usage, model, batch)
    # One log record per pass.
    append_run(log_path, {
        "date": datetime.date.today().isoformat(),
        "bom": source,
        "pass": name,
        "model": model,
        "batch": batch,
        "rows": sum(n for n, _ in shape),
        "shape": shape,
        "error_rows": error_rows,
        "estimate_low": round(est.low, 4),
        "estimate_high": round(est.high, 4),
        "actual_cost": round(actual, 4),
        "usage": asdict(usage),
    })
    # Calibrated prediction from runs logged before this one.
    calibrated = f", calibrated ${fit.predict(est.mid):.2f}" if fit else ""
    # Errored passes are logged but left out of the fit.
    note = f" ({error_rows} errored rows: excluded from calibration)" if error_rows else ""
    # Summary line for the caller to print.
    return f"Actual vs estimate ({model}): ${actual:.2f} vs {money(est)}{calibrated}{note}"
