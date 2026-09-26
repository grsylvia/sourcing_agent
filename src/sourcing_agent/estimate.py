"""Pre-run cost estimate: prices a BOM in batch and live mode without calling the API."""

# Sum usage fields.
from dataclasses import dataclass, fields
# Cache ages.
import datetime
# File paths.
from pathlib import Path

# Quote cache reader.
from .cache import load_cache
# BOM, suppliers, batching, and cache split shared with real runs.
from .orchestrator import load_bom, load_suppliers, make_batches, split_cached
# List-price costing.
from .pricing import estimate_cost
# Worker limits and models.
from .worker import ESCALATION_MODEL, MAX_PAGE_TOKENS, MAX_TURNS, MODEL, SEARCHES_PER_PART, Usage, category_suppliers

# Assumed sizes below are not yet measured; tune them after the first live runs.
# System prompt, tools, and task message per worker conversation.
PROMPT_TOKENS = 4_000
# Search-result tokens added to context per web search.
SEARCH_TOKENS = 3_000
# Page tokens per web fetch in the low case (the high case uses the MAX_PAGE_TOKENS cap).
FETCH_TOKENS_LOW = 5_000
# Output tokens (thinking, queries, submission) per row, low and high.
OUTPUT_PER_ROW = (3_000, 8_000)
# Times gathered context is re-read from the prompt cache, low and high.
REREADS = (1, 3)


# Cost range for one model in one mode.
@dataclass
class Range:
    # Low-case USD.
    low: float
    # High-case USD.
    high: float


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


def conversation_usage(n_rows: int, n_suppliers: int, high: bool) -> Usage:
    """Assumed usage of one worker conversation, low or high case."""
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


def total_usage(batches: list[tuple[str, list[dict]]], suppliers: list[dict], high: bool) -> Usage:
    """Sum assumed usage over all worker conversations."""
    # Running total.
    total = Usage()
    # Add each conversation.
    for category, rows in batches:
        u = conversation_usage(len(rows), len(category_suppliers(category, suppliers)), high)
        for f in fields(Usage):
            setattr(total, f.name, getattr(total, f.name) + getattr(u, f.name))
    # Totals.
    return total


def estimate_bom(bom_path: Path, suppliers_path: Path, cache_path: Path, max_age_days: int) -> Estimate:
    """Estimate the cost of sourcing a BOM in each mode, on each model."""
    # Approved suppliers and categories.
    config = load_suppliers(suppliers_path)
    # Checked BOM rows.
    rows = load_bom(bom_path, config["categories"])
    # Rows the cache already answers.
    cached, todo, _ = split_cached(rows, config, load_cache(cache_path), max_age_days, datetime.date.today())
    # Worker conversations a run would start.
    batches = make_batches(todo)
    # Low and high usage.
    low, high = total_usage(batches, config["suppliers"], False), total_usage(batches, config["suppliers"], True)
    # Cost per mode and model.
    costs = {
        (batch, model): Range(estimate_cost(low, model, batch), estimate_cost(high, model, batch))
        for batch in (True, False)
        for model in (MODEL, ESCALATION_MODEL)
    }
    # Rows to source per category, in BOM order.
    counts: dict[str, int] = {}
    for r in todo:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    # Category breakdown with supplier counts.
    categories = [(c, n, len(category_suppliers(c, config["suppliers"]))) for c, n in counts.items()]
    # The estimate.
    return Estimate(len(rows), len(cached), len(todo), len(batches), categories, costs)
