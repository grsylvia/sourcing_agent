"""In-memory BOM orchestration: cached quotes, sourcing passes, retries, and price selection."""

# Quote dates.
import datetime
# Progress messages.
import logging
# Whole-run summary record.
from dataclasses import dataclass
# Provider clients and completed-pass observer.
from typing import Any, Callable
# Default models and metered usage.
from ..core.agent import MODEL, ESCALATION_MODEL, Usage
# Output column contract.
from .bom import CBOM_COLUMNS
# Cache successful fresh quotes.
from .cache import store_parts
# Shared pass execution and learning facts.
from .execution import SourcingPass, source_rows
# Shared planning for execution and estimates.
from .planning import pass_shape, split_cached
# Deterministic price selection.
from .quotes import PartQuotes, pick_lowest

# Progress for the complete run.
log = logging.getLogger(__name__)


# Result of a whole run.
@dataclass
class RunSummary:
    # CBOM rows written.
    rows: list[dict]
    # Token and tool usage of the first pass.
    usage: Usage
    # Token and tool usage of the escalation retries.
    escalation_usage: Usage
    # Rows retried on the escalation model.
    escalated: int
    # (rows, suppliers) per worker conversation of the first pass.
    first_shape: list[tuple[int, int]]
    # (rows, suppliers) per worker conversation of the escalation pass.
    escalation_shape: list[tuple[int, int]]
    # Rows that errored in the first pass, before retries.
    first_errors: int
    # Rows that errored in the escalation pass.
    escalation_errors: int
    # Per-conversation records of the first pass.
    first_conversations: list[dict]
    # Per-conversation records of the escalation pass.
    escalation_conversations: list[dict]
    # Currency of every price.
    currency: str
    # Rows answered from the quote cache.
    reused: int
    # True when the Batch API (50% off) was used.
    batch: bool
    # First-pass model actually selected for this run.
    model: str = MODEL
    # Retry model actually selected for this run.
    escalation_model: str = ESCALATION_MODEL

def cbom_row(row: dict, part: PartQuotes | None, error: str | None, currency: str) -> dict:
    """Build one CBOM row from a BOM row and its worker result."""
    # Start with the BOM columns and blank sourcing columns.
    out = {c: row.get(c, "") for c in CBOM_COLUMNS}
    # Worker failed for this row.
    if error:
        return out | {"status": "error", "sourcing_notes": error}
    # Cheapest order across quotes.
    pick = pick_lowest(part, int(row["quantity"]))
    # No valid quotes.
    if pick is None:
        return out | {"status": "not_found", "quotes_compared": 0, "sourcing_notes": part.notes}
    # Units received.
    order_qty = pick.order_packs * pick.quote.pack_size
    # Sourced row.
    return out | {
        "status": "sourced",
        "vendor": pick.quote.vendor,
        "vendor_part_number": pick.quote.vendor_part_number,
        "url": pick.quote.url,
        "pack_size": pick.quote.pack_size,
        "order_packs": pick.order_packs,
        "order_qty": order_qty,
        "pack_price": f"{pick.pack_price:.2f}",
        "extended_price": f"{pick.extended_price:.2f}",
        "effective_unit_price": f"{pick.extended_price / order_qty:.4f}",
        "currency": currency,
        "quotes_compared": len(part.quotes),
        "quoted_at": part.quoted_at,
        "sourcing_notes": "; ".join(filter(None, [pick.quote.match_notes, part.notes])),
    }


async def source_bom(
    client: Any,
    rows: list[dict],
    config: dict,
    cache: dict,
    max_age_days: int,
    live: bool,
    escalate: bool = True,
    model: str = MODEL,
    escalation_model: str = ESCALATION_MODEL,
    on_pass: Callable[[SourcingPass], None] | None = None,
) -> RunSummary:
    """Reuse cached quotes, source the rest, retry failed rows on the escalation model, and build the CBOM rows."""
    # First-pass usage.
    usage = Usage()
    # Escalation usage, priced at the escalation model's rates.
    escalation_usage = Usage()
    # Today's date for cache ages and new quotes.
    today = datetime.date.today()
    # Cached quotes, rows to source, and cache keys.
    parts, todo, keys = split_cached(rows, config, cache, max_age_days, today)
    # Number of rows answered from the cache.
    reused = len(parts)
    # Log the reuse count.
    log.info("reusing cached quotes for %d of %d rows", reused, len(rows))
    # Fresh quotes, errors, and conversation records from this run.
    fresh: dict[str, PartQuotes] = {}
    errors: dict[str, str] = {}
    first_convs: list[dict] = []
    retry_convs: list[dict] = []
    # First pass on the default model.
    if todo:
        fresh, errors, first_convs = await source_rows(client, todo, config, live, model, usage)
        # Retain completed prices before retries or learning writes can fail.
        store_parts(cache, keys, fresh, today)
    # Persist completed first-pass evidence before retries or export can fail.
    if todo and on_pass:
        # Observers receive facts without controlling price selection.
        on_pass(SourcingPass("first", model, pass_shape(todo, config["suppliers"]), len(errors), usage, first_convs))
    # First-pass errors, before retries change them.
    first_errors = len(errors)
    # Escalation errors.
    retry_errors: dict[str, str] = {}
    # Rows the first model errored on or found no quotes for.
    retry = [r for r in todo if r["part_id"] in errors or not fresh[r["part_id"]].quotes] if escalate else []
    # Retry them once on the escalation model.
    if retry:
        # Announce the retry.
        log.info("retrying %d rows on %s", len(retry), escalation_model)
        # Second pass.
        retried, retry_errors, retry_convs = await source_rows(client, retry, config, live, escalation_model, escalation_usage)
        # Preserve valid retry prices before subsequent persistence steps.
        store_parts(cache, keys, retried, today)
        # Retain completed retry evidence independently of export.
        if on_pass:
            # The retry has its own model, usage, and failure count.
            on_pass(SourcingPass("escalation", escalation_model, pass_shape(retry, config["suppliers"]), len(retry_errors), escalation_usage, retry_convs))
        # Merge each retried row.
        for r in retry:
            # Part ID of the retried row.
            pid = r["part_id"]
            # The retry returned a result: it replaces the first pass.
            if pid in retried:
                retried[pid].notes = "; ".join(filter(None, [f"retried on {escalation_model}", retried[pid].notes]))
                fresh[pid] = retried[pid]
                errors.pop(pid, None)
            # The retry errored after a first-pass error: keep both messages.
            elif pid in errors:
                errors[pid] = f"{errors[pid]}; {escalation_model} retry: {retry_errors[pid]}"
            # The retry errored after a not-found: keep the first pass and note the error.
            else:
                fresh[pid].notes = "; ".join(filter(None, [fresh[pid].notes, f"{escalation_model} retry: {retry_errors[pid]}"]))
    # Date and cache each fresh result.
    store_parts(cache, keys, fresh, today)
    # All results by part ID.
    parts.update(fresh)
    # CBOM rows in BOM order.
    cbom = [cbom_row(r, parts.get(r["part_id"]), errors.get(r["part_id"]), config["currency"]) for r in rows]
    # Rows, usage, and run facts.
    return RunSummary(
        cbom, usage, escalation_usage, len(retry),
        pass_shape(todo, config["suppliers"]), pass_shape(retry, config["suppliers"]), first_errors, len(retry_errors),
        first_convs, retry_convs, config["currency"], reused, not live, model, escalation_model,
    )
