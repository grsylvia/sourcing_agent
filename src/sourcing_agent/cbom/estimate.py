"""CBOM cost estimate: prices sourcing passes from their shape; learning/ calibrates it and logs each pass."""

# Cache ages.
import datetime
# Sum usage fields.
from dataclasses import dataclass, fields
# File paths.
from pathlib import Path

# Models, turn cap, and usage totals.
from ..core.agent import ESCALATION_MODEL, MAX_TURNS, MODEL, Usage
# Supplier list and per-category suppliers.
from ..core.config import category_suppliers, load_suppliers
# Cost model and shared token assumptions.
from ..core.pricing import OUTPUT_PER_ROW, PROMPT_TOKENS, SEARCH_TOKENS, Range, estimate_cost
# Calibration and the per-pass comparison.
from ..learning.calibration import calibrate, compare_line
# Fitted line.
from ..learning.regression import Fit
# Run log.
from ..learning.runlog import record_pass
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


def calibrate_sourcing(log_path: Path) -> Fit | None:
    """Fit logged sourcing passes (first, escalation, trial) against this estimator."""
    # Learning fits the log with the sourcing estimator.
    return calibrate(log_path, shape_range)


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
    return Estimate(len(rows), len(cached), len(todo), len(shape), categories, costs, calibrate_sourcing(log_path))


def log_pass(log_path: Path, name: str, model: str, shape: list[tuple[int, int]], error_rows: int, usage: Usage, batch: bool, source: str, fit: Fit | None) -> str | None:
    """Record one sourcing pass for learning; return its actual-vs-estimate line, or None if it made no requests."""
    # Estimate for exactly these rows.
    est = shape_range(shape, model, batch)
    # Append the pass to the run log.
    actual = record_pass(log_path, source, name, model, batch, shape, error_rows, usage, est)
    # Comparison line, when the pass made requests.
    return compare_line(model, actual, est, fit, error_rows) if actual is not None else None
