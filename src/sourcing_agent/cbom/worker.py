"""Sourcing worker: quotes one batch of a BOM category from its approved suppliers (domain-locked web search)."""

# Prompt fingerprint for the settings tag.
import hashlib
# Serialize BOM rows into the prompt.
import json

# Agent loop types and the default model.
from ..core.agent import MODEL, Job
# Suppliers searched for a category.
from ..core.config import category_suppliers
# Worker failure type.
from ..core.errors import WorkerError
# Quote URL host check.
from ..core.web import domain_allowed
# Quote records.
from .quotes import PartQuotes, PriceBreak, Quote

# Web searches allowed per BOM row in one conversation.
SEARCHES_PER_PART = 6
# Largest fetched page, in tokens, allowed into context.
MAX_PAGE_TOKENS = 15000
# Client tool the worker uses to hand back its quotes.
SUBMIT_TOOL = "submit_quotes"

# Standing instructions for every sourcing worker.
SYSTEM_PROMPT = (
    "You are a purchasing agent. You find current catalog listings for BOM parts on approved "
    "supplier websites and report their pricing exactly as listed. You never estimate or invent "
    "prices, part numbers, or URLs."
)


def build_submit_tool(vendor_names: list[str]) -> dict:
    """Build the strict tool the worker calls once with all of its quotes (identical for every batch in a category)."""
    # Schema for one price break.
    price_break = {
        "type": "object",
        "additionalProperties": False,
        "required": ["min_packs", "pack_price"],
        "properties": {
            "min_packs": {"type": "integer", "description": "Smallest pack count this price applies to."},
            "pack_price": {"type": "number", "description": "Listed price of one pack at this break."},
        },
    }
    # Schema for one supplier listing.
    quote = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "vendor", "vendor_part_number", "url", "pack_size",
            "min_order_packs", "price_breaks", "match_notes",
        ],
        "properties": {
            "vendor": {"type": "string", "enum": vendor_names},
            "vendor_part_number": {"type": "string"},
            "url": {"type": "string", "description": "Product page URL where the price was seen."},
            "pack_size": {"type": "integer", "description": "Units per purchasable pack; 1 if sold singly."},
            "min_order_packs": {"type": "integer", "description": "Minimum order quantity, in packs."},
            "price_breaks": {"type": "array", "items": price_break},
            "match_notes": {"type": "string", "description": "How the listing matches or deviates from the spec."},
        },
    }
    # Schema for one BOM row's results.
    part = {
        "type": "object",
        "additionalProperties": False,
        "required": ["part_id", "quotes", "notes"],
        "properties": {
            "part_id": {"type": "string", "description": "The row's part_id exactly as given in the BOM rows."},
            "quotes": {"type": "array", "items": quote},
            "notes": {"type": "string", "description": "Why quotes are missing, or other caveats."},
        },
    }
    # Strict client tool so the submission always matches the schema.
    return {
        "name": SUBMIT_TOOL,
        "description": "Submit the final quotes for every BOM row in this batch. Call exactly once, when done searching.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["parts"],
            "properties": {"parts": {"type": "array", "items": part}},
        },
    }


def build_instructions(category: str, suppliers: list[dict], currency: str) -> str:
    """Build the task instructions, identical for every batch in a category so they cache once."""
    # One line per approved supplier with its domains.
    supplier_lines = "\n".join(f"- {s['name']}: {', '.join(s['domains'])}" for s in suppliers)
    # Task, suppliers, and procedure; the BOM rows follow in a separate block.
    return f"""Source the "{category}" BOM rows given after these instructions from the approved suppliers.

Approved suppliers (search and fetch only these):
{supplier_lines}

For each BOM row:
1. Search each approved supplier once for a listing that matches the description and spec.
2. Fetch a product page only when the search results do not show its price, pack size, and price breaks.
3. For each supplier, keep only its lowest-cost matching listing for the row's quantity.
4. Record the vendor part number, product URL, units per pack, minimum order in packs, and every quantity price break (price per pack, {currency}, US storefront).
5. Only report prices you saw on the supplier's page or search result. Skip listings with no visible price.
6. Note any spec deviation in match_notes.

When done, call {SUBMIT_TOOL} once with every row, using each row's part_id exactly as given. Give rows with no valid listing an empty quotes list and a short reason in notes."""


def build_rows(rows: list[dict]) -> str:
    """Build the per-batch block listing the BOM rows."""
    # One JSON line per BOM row.
    row_lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    # Rows block.
    return f"BOM rows (JSON, one per line):\n{row_lines}"


def prompt_hash() -> str:
    """Fingerprint of the system prompt, instructions template, and submit schema; changes whenever the prompts do."""
    # Template text with placeholders instead of per-batch values.
    text = SYSTEM_PROMPT + build_instructions("{category}", [], "{currency}") + json.dumps(build_submit_tool(["{vendor}"]), sort_keys=True)
    # First 8 hex digits.
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def check_quote(q: dict, domains_by_vendor: dict[str, list[str]]) -> str | None:
    """Return a rejection reason for an invalid quote, or None if it is valid."""
    # Vendor must be one of this category's suppliers.
    if q["vendor"] not in domains_by_vendor:
        return "vendor not approved for this category"
    # URL must sit on that vendor's approved domains.
    if not domain_allowed(q["url"], domains_by_vendor[q["vendor"]]):
        return f"URL not on {q['vendor']} domains"
    # Pack size and minimum order must be positive.
    if q["pack_size"] < 1 or q["min_order_packs"] < 1:
        return "pack size or minimum order below 1"
    # At least one price break is required.
    if not q["price_breaks"]:
        return "no price breaks"
    # Every break needs a positive pack count and price.
    if any(b["min_packs"] < 1 or b["pack_price"] <= 0 for b in q["price_breaks"]):
        return "invalid price break"
    # Quote passed every check.
    return None


def parse_submission(tool_input: dict, rows: list[dict], suppliers: list[dict]) -> list[PartQuotes]:
    """Validate the worker's submission and convert it to PartQuotes, one per BOM row."""
    # Approved domains keyed by vendor name.
    domains_by_vendor = {s["name"]: s["domains"] for s in suppliers}
    # Start with an empty result for every requested row.
    results = {r["part_id"]: PartQuotes(r["part_id"], notes="not returned by worker") for r in rows}
    # Walk each row the worker returned.
    for part in tool_input["parts"]:
        # Ignore rows that were not requested.
        if part["part_id"] not in results:
            continue
        # Fresh result for this row with the worker's notes.
        result = PartQuotes(part["part_id"], notes=part["notes"])
        # Rejection reasons collected for this row.
        rejected = []
        # Validate each quote.
        for q in part["quotes"]:
            # Reason the quote is invalid, if any.
            reason = check_quote(q, domains_by_vendor)
            # Record rejected quotes in the notes.
            if reason:
                rejected.append(f"{q['vendor']} {q['vendor_part_number']}: {reason}")
                continue
            # Price breaks sorted by pack count.
            breaks = sorted((PriceBreak(b["min_packs"], b["pack_price"]) for b in q["price_breaks"]), key=lambda b: b.min_packs)
            # Keep the valid quote.
            result.quotes.append(Quote(
                part_id=part["part_id"],
                vendor=q["vendor"],
                vendor_part_number=q["vendor_part_number"],
                url=q["url"],
                pack_size=q["pack_size"],
                min_order_packs=q["min_order_packs"],
                price_breaks=breaks,
                match_notes=q["match_notes"],
            ))
        # Append rejection reasons to the notes.
        if rejected:
            result.notes = "; ".join(filter(None, [result.notes, "rejected: " + "; ".join(rejected)]))
        # Store the row's result.
        results[part["part_id"]] = result
    # Results in BOM order.
    return [results[r["part_id"]] for r in rows]


def new_job(category: str, rows: list[dict], suppliers: list[dict], currency: str, model: str = MODEL) -> Job:
    """Set up the tools and first message for one sourcing conversation."""
    # Suppliers searched for this category.
    approved = category_suppliers(category, suppliers)
    # A category with no suppliers cannot be sourced.
    if not approved:
        raise WorkerError(f"no approved suppliers for category '{category}'")
    # Every domain the web tools may reach.
    domains = sorted({d for s in approved for d in s["domains"]})
    # Server-side web search, locked to approved domains.
    web_search = {
        "type": "web_search_20260209",
        "name": "web_search",
        "allowed_domains": domains,
        "max_uses": SEARCHES_PER_PART * len(rows),
        "user_location": {"type": "approximate", "country": "US"},
    }
    # Server-side web fetch, locked to approved domains, with a page-size cap.
    web_fetch = {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "allowed_domains": domains,
        "max_content_tokens": MAX_PAGE_TOKENS,
    }
    # Conversation starts with the batch task.
    first = {"role": "user", "content": [
        # Shared per-category prefix (tools, system, instructions) ends here, so later batches read it from the cache.
        {"type": "text", "text": build_instructions(category, approved, currency), "cache_control": {"type": "ephemeral"}},
        # Rows that differ per batch go after the breakpoint.
        {"type": "text", "text": build_rows(rows)},
    ]}
    # The ready-to-run job, parsing its submission into quotes per row.
    return Job(
        label=f"{category} [{', '.join(r['part_id'] for r in rows)}]",
        system=SYSTEM_PROMPT,
        tools=[web_search, web_fetch, build_submit_tool([s["name"] for s in approved])],
        messages=[first],
        submit_tool=SUBMIT_TOOL,
        parse=lambda tool_input: parse_submission(tool_input, rows, approved),
        model=model,
        meta={"category": category, "rows": len(rows), "suppliers": len(approved)},
    )
