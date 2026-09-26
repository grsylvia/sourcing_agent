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
# Corrupt .xlsx errors.
import zipfile
# Pick records.
from dataclasses import dataclass
# File paths.
from pathlib import Path

# Async Claude client and API errors.
import anthropic
# Excel BOM files.
import openpyxl
# Unsupported workbook errors.
from openpyxl.utils.exceptions import InvalidFileException

# Batch-mode runner.
from .batch import run_jobs
# Quote cache helpers.
from .cache import load_cache, lookup, quote_key, save_cache, store
# Worker API and records.
from .worker import ESCALATION_MODEL, MODEL, PartQuotes, PriceBreak, Quote, Usage, WorkerError, new_job, source_category

# BOM rows per worker conversation; small batches stop pages being re-read.
ROWS_PER_WORKER = 2
# Worker conversations running at once in live mode.
MAX_PARALLEL = 4
# BOM input columns, in order.
BOM_COLUMNS = ["part_id", "description", "category", "quantity", "spec", "mfr_part_number", "notes"]
# BOM file extensions read as Excel workbooks.
XLSX_SUFFIXES = {".xlsx", ".xlsm"}
# Workbook sheet holding the BOM.
XLSX_SHEET = "BOM"
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
    # Token and tool usage of the first pass.
    usage: Usage
    # Token and tool usage of the escalation retries.
    escalation_usage: Usage
    # Rows retried on the escalation model.
    escalated: int
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


def cell_text(value: object) -> str:
    """Return an Excel cell value as BOM text."""
    # Empty cell.
    if value is None:
        return ""
    # Whole-number floats print without ".0".
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    # Anything else as plain text.
    return str(value)


def read_csv_rows(path: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """Return the header and (line, row) pairs from a BOM CSV."""
    # Read all rows.
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            header = reader.fieldnames or []
    except OSError as e:
        raise InputError(f"cannot read {path}: {e}") from e
    # Line 2 is the first data row.
    return header, list(enumerate(rows, start=2))


def read_xlsx_rows(path: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """Return the header and (row number, row) pairs from the BOM sheet of a workbook."""
    # Open for reading, with formula results instead of formulas.
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, zipfile.BadZipFile, InvalidFileException) as e:
        raise InputError(f"cannot read {path}: {e}") from e
    # The BOM sheet, or the first sheet if there is none.
    sheet = book[XLSX_SHEET] if XLSX_SHEET in book.sheetnames else book.worksheets[0]
    # All cells as trimmed text.
    values = [[cell_text(v).strip() for v in r] for r in sheet.iter_rows(values_only=True)]
    # Release the file.
    book.close()
    # Row 1 is the header.
    header = values[0] if values else []
    # Data rows by sheet row number, skipping blank rows.
    return header, [(n, dict(zip(header, v))) for n, v in enumerate(values[1:], start=2) if any(v)]


def load_bom(path: Path, categories: list[str]) -> list[dict]:
    """Read a BOM CSV or .xlsx, check every row, and return rows with all BOM columns."""
    # Read the header and numbered rows for the file type.
    header, numbered = read_xlsx_rows(path) if path.suffix.lower() in XLSX_SUFFIXES else read_csv_rows(path)
    # Required columns must be in the header.
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise InputError(f"{path}: missing columns {missing}")
    # Part IDs seen so far.
    seen = set()
    # Check each row.
    for line, row in numbered:
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
    return [{c: row[c] for c in BOM_COLUMNS} for _, row in numbered]


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


async def _run_live(client: anthropic.AsyncAnthropic, batches: list[tuple[str, list[dict]]], config: dict, usage: Usage, model: str) -> list[list[PartQuotes] | str]:
    """Live mode: stream each batch's conversation, a few at a time."""
    # Limit concurrent worker conversations.
    gate = asyncio.Semaphore(MAX_PARALLEL)

    async def run_one(category: str, batch: list[dict]) -> list[PartQuotes] | str:
        """Run one batch; return its quotes or an error message."""
        # Wait for a free slot.
        async with gate:
            try:
                # Run the worker.
                return await source_category(client, category, batch, config["suppliers"], config["currency"], usage, model)
            except anthropic.AuthenticationError:
                # Bad credentials stop the whole run.
                raise
            except (WorkerError, anthropic.APIError) as e:
                # Other failures mark only this batch as errored.
                return str(e)

    # Run all batches.
    return await asyncio.gather(*(run_one(c, b) for c, b in batches))


async def _run_batch(client: anthropic.AsyncAnthropic, batches: list[tuple[str, list[dict]]], config: dict, usage: Usage, model: str) -> list[list[PartQuotes] | str]:
    """Batch mode: build one job per batch and run them through the Batch API."""
    # Result slot per batch.
    outcomes: list[list[PartQuotes] | str] = [""] * len(batches)
    # Jobs that could be set up, with their batch index.
    jobs = []
    # Build each job.
    for i, (category, batch) in enumerate(batches):
        try:
            # Tools and first message for this batch.
            jobs.append((i, new_job(category, batch, config["suppliers"], config["currency"], model)))
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


def split_cached(rows: list[dict], config: dict, cache: dict, max_age_days: int, today: datetime.date) -> tuple[dict[str, PartQuotes], list[dict], dict[str, str]]:
    """Return reusable cached quotes by part ID, the rows still to source, and each row's cache key."""
    # Cache key per part ID.
    keys = {r["part_id"]: quote_key(r, config["suppliers"]) for r in rows}
    # Quotes reused from the cache.
    cached = {r["part_id"]: hit for r in rows if (hit := lookup(cache, keys[r["part_id"]], max_age_days, today))}
    # Rows the cache does not answer.
    return cached, [r for r in rows if r["part_id"] not in cached], keys


async def _source_rows(
    client: anthropic.AsyncAnthropic,
    rows: list[dict],
    config: dict,
    live: bool,
    model: str,
    usage: Usage,
) -> tuple[dict[str, PartQuotes], dict[str, str]]:
    """Source rows on one model; return quotes and error messages by part ID."""
    # Worker-sized batches by category.
    batches = make_batches(rows)
    # Pick the runner for the mode.
    runner = _run_live if live else _run_batch
    # One outcome per batch.
    outcomes = await runner(client, batches, config, usage, model)
    # Quotes and errors by part ID.
    parts: dict[str, PartQuotes] = {}
    errors: dict[str, str] = {}
    # Sort outcomes into quotes and errors.
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
        fresh, errors = await _source_rows(client, todo, config, live, MODEL, usage)
    # Rows the first model errored on or found no quotes for.
    retry = [r for r in todo if r["part_id"] in errors or not fresh[r["part_id"]].quotes] if escalate else []
    # Retry them once on the escalation model.
    if retry:
        # Announce the retry.
        log.info("retrying %d rows on %s", len(retry), ESCALATION_MODEL)
        # Second pass.
        retried, retry_errors = await _source_rows(client, retry, config, live, ESCALATION_MODEL, escalation_usage)
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
    return RunSummary(cbom, usage, escalation_usage, len(retry), config["currency"], reused, not live)


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
