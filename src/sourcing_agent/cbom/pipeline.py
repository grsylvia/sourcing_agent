"""CBOM pipeline: reuse cached quotes, source the rest in category batches, retry failures on the escalation model, build the CBOM."""

# Quote date for the CBOM.
import datetime
# Progress lines.
import logging
# Run summary record.
from dataclasses import dataclass
# File paths.
from pathlib import Path

# Async Claude client.
import anthropic

# Models and usage totals.
from ..core.agent import ESCALATION_MODEL, MODEL, Usage
# Supplier list and per-category suppliers.
from ..core.config import category_suppliers, load_suppliers
# Worker failure type.
from ..core.errors import WorkerError
# Live and batch runners.
from ..core.runner import run_jobs
# BOM and CBOM files.
from .bom import CBOM_COLUMNS, load_bom, write_cbom
# Quote cache helpers.
from .cache import load_cache, lookup, quote_key, save_cache, store
# Quote records and the price rule.
from .quotes import PartQuotes, pick_lowest
# Sourcing worker jobs.
from .worker import new_job

# BOM rows per worker conversation; small batches stop pages being re-read.
ROWS_PER_WORKER = 2

# Logger for progress lines.
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
    # Currency of every price.
    currency: str
    # Rows answered from the quote cache.
    reused: int
    # True when the Batch API (50% off) was used.
    batch: bool


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


def split_cached(rows: list[dict], config: dict, cache: dict, max_age_days: int, today: datetime.date) -> tuple[dict[str, PartQuotes], list[dict], dict[str, str]]:
    """Return reusable cached quotes by part ID, the rows still to source, and each row's cache key."""
    # Cache key per part ID.
    keys = {r["part_id"]: quote_key(r, config["suppliers"]) for r in rows}
    # Quotes reused from the cache.
    cached = {r["part_id"]: hit for r in rows if (hit := lookup(cache, keys[r["part_id"]], max_age_days, today))}
    # Rows the cache does not answer.
    return cached, [r for r in rows if r["part_id"] not in cached], keys


async def source_rows(
    client: anthropic.AsyncAnthropic,
    rows: list[dict],
    config: dict,
    live: bool,
    model: str,
    usage: Usage,
) -> tuple[dict[str, PartQuotes], dict[str, str]]:
    """Source rows on one model with the config's suppliers; return quotes and error messages by part ID."""
    # Worker-sized batches by category.
    batches = make_batches(rows)
    # Outcome per batch: quotes, or an error message.
    outcomes: list[list[PartQuotes] | str] = [""] * len(batches)
    # Jobs that could be set up, with their batch index.
    jobs = []
    for i, (category, batch) in enumerate(batches):
        try:
            # Tools and first message for this batch.
            jobs.append((i, new_job(category, batch, config["suppliers"], config["currency"], model)))
        except WorkerError as e:
            # No suppliers for the category.
            outcomes[i] = str(e)
    # Run the jobs and put each result in its batch slot.
    for (i, _), result in zip(jobs, await run_jobs(client, [job for _, job in jobs], live, usage)):
        outcomes[i] = result
    # Quotes and errors by part ID.
    parts: dict[str, PartQuotes] = {}
    errors: dict[str, str] = {}
    for (category, batch), outcome in zip(batches, outcomes):
        # A string outcome is an error for every row in the batch.
        if isinstance(outcome, str):
            log.warning("%s %s [%s]: error: %s", model, category, ", ".join(r["part_id"] for r in batch), outcome)
            errors.update({r["part_id"]: outcome for r in batch})
            continue
        # Quotes for each row in the batch.
        parts.update({part.part_id: part for part in outcome})
    # Results for every row.
    return parts, errors


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
    client: anthropic.AsyncAnthropic,
    rows: list[dict],
    config: dict,
    cache: dict,
    max_age_days: int,
    live: bool,
    escalate: bool = True,
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
    # Fresh quotes and errors from this run.
    fresh: dict[str, PartQuotes] = {}
    errors: dict[str, str] = {}
    # First pass on the default model.
    if todo:
        fresh, errors = await source_rows(client, todo, config, live, MODEL, usage)
    # First-pass errors, before retries change them.
    first_errors = len(errors)
    # Escalation errors.
    retry_errors: dict[str, str] = {}
    # Rows the first model errored on or found no quotes for.
    retry = [r for r in todo if r["part_id"] in errors or not fresh[r["part_id"]].quotes] if escalate else []
    # Retry them once on the escalation model.
    if retry:
        # Announce the retry.
        log.info("retrying %d rows on %s", len(retry), ESCALATION_MODEL)
        # Second pass.
        retried, retry_errors = await source_rows(client, retry, config, live, ESCALATION_MODEL, escalation_usage)
        # Merge each retried row.
        for r in retry:
            # Part ID of the retried row.
            pid = r["part_id"]
            # The retry returned a result: it replaces the first pass.
            if pid in retried:
                retried[pid].notes = "; ".join(filter(None, [f"retried on {ESCALATION_MODEL}", retried[pid].notes]))
                fresh[pid] = retried[pid]
                errors.pop(pid, None)
            # The retry errored after a first-pass error: keep both messages.
            elif pid in errors:
                errors[pid] = f"{errors[pid]}; {ESCALATION_MODEL} retry: {retry_errors[pid]}"
            # The retry errored after a not-found: keep the first pass and note the error.
            else:
                fresh[pid].notes = "; ".join(filter(None, [fresh[pid].notes, f"{ESCALATION_MODEL} retry: {retry_errors[pid]}"]))
    # Date and cache each fresh result.
    for pid, part in fresh.items():
        part.quoted_at = today.isoformat()
        store(cache, keys[pid], part)
    # All results by part ID.
    parts.update(fresh)
    # CBOM rows in BOM order.
    cbom = [cbom_row(r, parts.get(r["part_id"]), errors.get(r["part_id"]), config["currency"]) for r in rows]
    # Rows, usage, and run facts.
    return RunSummary(
        cbom, usage, escalation_usage, len(retry),
        pass_shape(todo, config["suppliers"]), pass_shape(retry, config["suppliers"]), first_errors, len(retry_errors),
        config["currency"], reused, not live,
    )


async def run_sourcing(
    bom_path: Path,
    suppliers_path: Path,
    out_path: Path,
    cache_path: Path,
    max_age_days: int = 7,
    live: bool = False,
    escalate: bool = True,
) -> RunSummary:
    """Load inputs, source the BOM, save the quote cache, and write the CBOM."""
    # Approved suppliers and categories.
    config = load_suppliers(suppliers_path)
    # Checked BOM rows.
    rows = load_bom(bom_path, config["categories"])
    # Previously gathered quotes.
    cache = load_cache(cache_path)
    # One client shared by all workers.
    async with anthropic.AsyncAnthropic() as client:
        try:
            # Source every row.
            summary = await source_bom(client, rows, config, cache, max_age_days, live, escalate)
        finally:
            # Keep whatever was quoted, even if the run stopped early.
            save_cache(cache_path, cache)
    # Save the CBOM.
    write_cbom(out_path, summary.rows)
    # Rows and usage for the caller.
    return summary
