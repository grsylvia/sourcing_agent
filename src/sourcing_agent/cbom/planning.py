"""Shared deterministic planning for estimates and execution."""

# Cache age comparisons.
import datetime
# Provider settings recorded with each pass.
from ..core.agent import EFFORT, MAX_TURNS
# Supplier counts per category.
from ..core.config import category_suppliers
# Provider identity for settings.
from ..core.providers import provider_of
# Reusable quotes and cache identities.
from .cache import lookup, quote_key
# Cached quote record type.
from .quotes import PartQuotes
# Worker settings shared with estimates.
from .worker import MAX_PAGE_TOKENS, SEARCHES_PER_PART, prompt_hash

# Keep conversations small to limit repeated page context.
ROWS_PER_WORKER = 2


def make_batches(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group rows by category, then split each group into worker-sized batches."""
    # Rows per category, in BOM order.
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["category"], []).append(row)
    # Fixed-size batches within each category.
    return [
        (category, group[i:i + ROWS_PER_WORKER])
        for category, group in groups.items()
        for i in range(0, len(group), ROWS_PER_WORKER)
    ]


def pass_shape(rows: list[dict], suppliers: list[dict]) -> list[tuple[int, int]]:
    """Return (rows, approved suppliers) for each worker conversation these rows would start."""
    # One entry per batch.
    return [(len(batch), len(category_suppliers(category, suppliers))) for category, batch in make_batches(rows)]


def sourcing_settings(model: str, batch: bool) -> dict:
    """Settings a sourcing pass runs with, logged so learning can compare them."""
    # Everything that changes how many tokens a conversation uses.
    return {
        "model": model,
        "provider": provider_of(model),
        "web_adapter": "responses-v1" if provider_of(model) == "openai" else "messages",
        "batch": batch,
        "effort": EFFORT,
        "max_turns": MAX_TURNS,
        "rows_per_worker": ROWS_PER_WORKER,
        "searches_per_part": SEARCHES_PER_PART,
        "page_tokens": None if provider_of(model) == "openai" else MAX_PAGE_TOKENS,
        "prompt": prompt_hash(),
    }


def split_cached(rows: list[dict], config: dict, cache: dict, max_age_days: int, today: datetime.date) -> tuple[dict[str, PartQuotes], list[dict], dict[str, str]]:
    """Return reusable cached quotes by part ID, the rows still to source, and each row's cache key."""
    # Cache key per part ID.
    keys = {r["part_id"]: quote_key(r, config["suppliers"]) for r in rows}
    # Quotes reused from the cache.
    cached = {r["part_id"]: hit for r in rows if (hit := lookup(cache, keys[r["part_id"]], max_age_days, today))}
    # Rows the cache does not answer.
    return cached, [r for r in rows if r["part_id"] not in cached], keys
