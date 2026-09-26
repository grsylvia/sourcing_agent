"""Orchestrator: splits a BOM into category batches, runs workers in parallel, and writes the CBOM."""

# Parallel workers.
import asyncio
# BOM and CBOM files.
import csv
# Quote date for the CBOM.
import datetime
# Progress lines.
import logging
# Round pack counts up.
import math
# Supplier list file.
import tomllib
# Pick records.
from dataclasses import dataclass
# File paths.
from pathlib import Path

# Async Claude client and API errors.
import anthropic

# Batch-mode runner.
from .batch import run_jobs
# Quote cache helpers.
from .cache import load_cache, lookup, quote_key, save_cache, store
# Worker API and records.
from .worker import PartQuotes, PriceBreak, Quote, Usage, WorkerError, new_job, source_category

# BOM rows per worker conversation; small batches stop pages being re-read.
ROWS_PER_WORKER = 2
# Worker conversations running at once in live mode.
MAX_PARALLEL = 4
# BOM input columns, in order.
BOM_COLUMNS = ["part_id", "description", "category", "quantity", "spec", "mfr_part_number", "notes"]
# BOM columns that must have a value.
REQUIRED_COLUMNS = ["part_id", "description", "category", "quantity"]
# CBOM output columns, in order.
CBOM_COLUMNS = BOM_COLUMNS + [
    "status", "vendor", "vendor_part_number", "url", "pack_size", "order_packs", "order_qty",
    "pack_price", "extended_price", "effective_unit_price", "currency", "quotes_compared",
    "quoted_at", "sourcing_notes",
]

# Logger for progress lines.
log = logging.getLogger(__name__)


# Raised for an unreadable or invalid BOM or supplier file.
class InputError(Exception):
    pass


# The cheapest way to order one quote at the BOM quantity.
@dataclass
class Pick:
    # Quote being ordered.
    quote: Quote
    # Packs to order.
    order_packs: int
    # Price per pack at that order size.
    pack_price: float
    # Total for the order (shipping excluded).
    extended_price: float


# Result of a whole run.
@dataclass
class RunSummary:
    # CBOM rows written.
    rows: list[dict]
    # Token and tool usage across all workers.
    usage: Usage
    # Currency of every price.
    currency: str
    # Rows answered from the quote cache.
    reused: int
    # True when the Batch API (50% off) was used.
    batch: bool


def load_suppliers(path: Path) -> dict:
    """Read suppliers.toml and check its required keys."""
    # Parse the TOML file.
    try:
        config = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise InputError(f"cannot read {path}: {e}") from e
    # Top-level keys the run needs.
    for key in ("currency", "categories", "suppliers"):
        if key not in config:
            raise InputError(f"{path}: missing '{key}'")
    # Keys every supplier needs.
    for s in config["suppliers"]:
        if not {"name", "domains", "categories"} <= s.keys():
            raise InputError(f"{path}: supplier {s.get('name', '?')} needs name, domains, categories")
    # Valid configuration.
    return config


def load_bom(path: Path, categories: list[str]) -> list[dict]:
    """Read bom.csv, check every row, and return rows with all BOM columns."""
    # Read all rows.
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            header = reader.fieldnames or []
    except OSError as e:
        raise InputError(f"cannot read {path}: {e}") from e
    # Required columns must be in the header.
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise InputError(f"{path}: missing columns {missing}")
    # Part IDs seen so far.
    seen = set()
    # Check each row (line 2 is the first data row).
    for line, row in enumerate(rows, start=2):
        # Fill absent optional columns and trim values.
        row.update({c: (row.get(c) or "").strip() for c in BOM_COLUMNS})
        # Required values must be present.
        if not all(row[c] for c in REQUIRED_COLUMNS):
            raise InputError(f"{path}:{line}: empty required value")
        # Part IDs must be unique.
        if row["part_id"] in seen:
            raise InputError(f"{path}:{line}: duplicate part_id {row['part_id']}")
        # Remember this part ID.
        seen.add(row["part_id"])
        # Category must be in suppliers.toml.
        if row["category"] not in categories:
            raise InputError(f"{path}:{line}: unknown category '{row['category']}'")
        # Quantity must be a positive integer.
        if not row["quantity"].isdigit() or int(row["quantity"]) < 1:
            raise InputError(f"{path}:{line}: quantity must be a positive integer")
    # Rows with only the BOM columns.
    return [{c: row[c] for c in BOM_COLUMNS} for row in rows]


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


def price_at(breaks: list[PriceBreak], packs: int) -> float:
    """Return the pack price for an order of this many packs."""
    # Highest break the order qualifies for (breaks sorted by min_packs).
    return [b for b in breaks if b.min_packs <= packs][-1].pack_price


def best_order(quote: Quote, quantity: int) -> Pick:
    """Find the cheapest order of this quote that covers the quantity."""
    # Packs needed, raised to the minimum order and the first price break.
    needed = max(math.ceil(quantity / quote.pack_size), quote.min_order_packs, quote.price_breaks[0].min_packs)
    # Order sizes to try: the need, and each larger break that might cost less in total.
    sizes = {needed} | {b.min_packs for b in quote.price_breaks if b.min_packs > needed}
    # Total cost for each order size.
    totals = [(packs * price_at(quote.price_breaks, packs), packs) for packs in sizes]
    # Cheapest total, fewest packs on a tie.
    total, packs = min(totals)
    # The chosen order.
    return Pick(quote, packs, price_at(quote.price_breaks, packs), total)


def pick_lowest(part: PartQuotes, quantity: int) -> Pick | None:
    """Return the lowest-total order across all quotes, or None if there are none."""
    # Best order per quote.
    picks = [best_order(q, quantity) for q in part.quotes]
    # Lowest extended price wins.
    return min(picks, key=lambda p: p.extended_price, default=None)


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


async def _run_live(client: anthropic.AsyncAnthropic, batches: list[tuple[str, list[dict]]], config: dict, usage: Usage) -> list[list[PartQuotes] | str]:
    """Live mode: stream each batch's conversation, a few at a time."""
    # Limit concurrent worker conversations.
    gate = asyncio.Semaphore(MAX_PARALLEL)

    async def run_one(category: str, batch: list[dict]) -> list[PartQuotes] | str:
        """Run one batch; return its quotes or an error message."""
        # Wait for a free slot.
        async with gate:
            try:
                # Run the worker.
                return await source_category(client, category, batch, config["suppliers"], config["currency"], usage)
            except anthropic.AuthenticationError:
                # Bad credentials stop the whole run.
                raise
            except (WorkerError, anthropic.APIError) as e:
                # Other failures mark only this batch as errored.
                return str(e)

    # Run all batches.
    return await asyncio.gather(*(run_one(c, b) for c, b in batches))


async def _run_batch(client: anthropic.AsyncAnthropic, batches: list[tuple[str, list[dict]]], config: dict, usage: Usage) -> list[list[PartQuotes] | str]:
    """Batch mode: build one job per batch and run them through the Batch API."""
    # Result slot per batch.
    outcomes: list[list[PartQuotes] | str] = [""] * len(batches)
    # Jobs that could be set up, with their batch index.
    jobs = []
    # Build each job.
    for i, (category, batch) in enumerate(batches):
        try:
            # Tools and first message for this batch.
            jobs.append((i, new_job(category, batch, config["suppliers"], config["currency"])))
        except WorkerError as e:
            # No suppliers for the category.
            outcomes[i] = str(e)
    # Run the jobs, if any.
    if jobs:
        # Results in job order.
        results = await run_jobs(client, [job for _, job in jobs], usage)
        # Put each result in its batch slot.
        for (i, _), result in zip(jobs, results):
            outcomes[i] = result
    # One outcome per batch.
    return outcomes


async def source_bom(
    client: anthropic.AsyncAnthropic,
    rows: list[dict],
    config: dict,
    cache: dict,
    max_age_days: int,
    live: bool,
) -> RunSummary:
    """Reuse cached quotes, source the remaining rows, and build the CBOM rows."""
    # Usage shared by all workers.
    usage = Usage()
    # Today's date for cache ages and new quotes.
    today = datetime.date.today()
    # Cache key per part ID.
    keys = {r["part_id"]: quote_key(r, config["suppliers"]) for r in rows}
    # Quotes reused from the cache.
    parts = {r["part_id"]: hit for r in rows if (hit := lookup(cache, keys[r["part_id"]], max_age_days, today))}
    # Number of rows answered from the cache.
    reused = len(parts)
    # Log the reuse count.
    log.info("reusing cached quotes for %d of %d rows", reused, len(rows))
    # Batches for rows that still need sourcing.
    batches = make_batches([r for r in rows if r["part_id"] not in parts])
    # Error message per part ID.
    errors: dict[str, str] = {}
    # Source the remaining rows.
    if batches:
        # Pick the runner for the mode.
        runner = _run_live if live else _run_batch
        # One outcome per batch.
        outcomes = await runner(client, batches, config, usage)
        # Sort outcomes into quotes and errors.
        for (category, batch), outcome in zip(batches, outcomes):
            # A string outcome is an error for every row in the batch.
            if isinstance(outcome, str):
                log.warning("%s [%s]: error: %s", category, ", ".join(r["part_id"] for r in batch), outcome)
                errors.update({r["part_id"]: outcome for r in batch})
                continue
            # Date and cache each fresh result.
            for part in outcome:
                part.quoted_at = today.isoformat()
                parts[part.part_id] = part
                store(cache, keys[part.part_id], part)
    # CBOM rows in BOM order.
    cbom = [cbom_row(r, parts.get(r["part_id"]), errors.get(r["part_id"]), config["currency"]) for r in rows]
    # Rows, usage, and run facts.
    return RunSummary(cbom, usage, config["currency"], reused, not live)


def write_cbom(path: Path, rows: list[dict]) -> None:
    """Write the CBOM rows to a CSV file."""
    # Create the output folder if needed.
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write header and rows.
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CBOM_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


async def run_sourcing(
    bom_path: Path,
    suppliers_path: Path,
    out_path: Path,
    cache_path: Path,
    max_age_days: int = 7,
    live: bool = False,
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
            summary = await source_bom(client, rows, config, cache, max_age_days, live)
        finally:
            # Keep whatever was quoted, even if the run stopped early.
            save_cache(cache_path, cache)
    # Save the CBOM.
    write_cbom(out_path, summary.rows)
    # Rows and usage for the caller.
    return summary
