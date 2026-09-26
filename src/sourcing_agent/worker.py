"""Category worker: sources one batch of a BOM category from its approved suppliers."""

# Parse quote URLs to check their host against approved domains.
from urllib.parse import urlparse
# Typed records for quotes returned to the orchestrator.
from dataclasses import dataclass, field
# Serialize BOM rows into the prompt.
import json
# Per-request usage lines.
import logging

# Async Claude client used in live mode.
import anthropic

# Model that runs each worker.
MODEL = "claude-sonnet-5"
# Thinking depth.
EFFORT = "medium"
# Output cap per request, with room for thinking.
MAX_TOKENS = 64000
# Web searches allowed per BOM row in one request.
SEARCHES_PER_PART = 6
# Cap on request round-trips (pause_turn resumes plus nudges).
MAX_TURNS = 6
# Largest fetched page, in tokens, allowed into context.
MAX_PAGE_TOKENS = 15000
# Name of the client tool the worker uses to hand back its results.
SUBMIT_TOOL = "submit_quotes"

# Standing instructions for every worker.
SYSTEM_PROMPT = (
    "You are a purchasing agent. You find current catalog listings for BOM parts on approved "
    "supplier websites and report their pricing exactly as listed. You never estimate or invent "
    "prices, part numbers, or URLs."
)

# Logger for per-request usage.
log = logging.getLogger(__name__)


# One quantity price break for a listing.
@dataclass
class PriceBreak:
    # Smallest number of packs this price applies to.
    min_packs: int
    # Price of one pack at this break.
    pack_price: float


# One supplier listing that matches a BOM row.
@dataclass
class Quote:
    # BOM row this listing is for.
    part_id: str
    # Supplier name from suppliers.toml.
    vendor: str
    # Supplier SKU or catalog number.
    vendor_part_number: str
    # Product page URL on an approved domain.
    url: str
    # Units per purchasable pack (1 if sold singly).
    pack_size: int
    # Minimum order, in packs.
    min_order_packs: int
    # Quantity price breaks, lowest min_packs first.
    price_breaks: list[PriceBreak]
    # How the listing matches or deviates from the spec.
    match_notes: str


# All quotes found for one BOM row.
@dataclass
class PartQuotes:
    # BOM row these quotes are for.
    part_id: str
    # Valid quotes, at most one per vendor.
    quotes: list[Quote] = field(default_factory=list)
    # Worker notes plus any rejected-quote reasons.
    notes: str = ""
    # Date the quotes were gathered (ISO format).
    quoted_at: str = ""


# Token and tool usage summed over requests.
@dataclass
class Usage:
    # Messages API requests made.
    requests: int = 0
    # Uncached input tokens.
    input_tokens: int = 0
    # Input tokens written to the prompt cache.
    cache_write_tokens: int = 0
    # Input tokens read from the prompt cache.
    cache_read_tokens: int = 0
    # Output tokens, including thinking.
    output_tokens: int = 0
    # Web searches run (billed per search).
    web_searches: int = 0
    # Web pages fetched.
    web_fetches: int = 0

    def add(self, u) -> None:
        """Add one response's usage to the totals."""
        # Count the request.
        self.requests += 1
        # Uncached input.
        self.input_tokens += u.input_tokens or 0
        # Cache writes.
        self.cache_write_tokens += u.cache_creation_input_tokens or 0
        # Cache reads.
        self.cache_read_tokens += u.cache_read_input_tokens or 0
        # Output tokens.
        self.output_tokens += u.output_tokens or 0
        # Server tool counts, when present.
        if u.server_tool_use:
            # Searches run.
            self.web_searches += u.server_tool_use.web_search_requests or 0
            # Pages fetched.
            self.web_fetches += u.server_tool_use.web_fetch_requests or 0


# Raised when a worker cannot produce a result.
class WorkerError(Exception):
    pass


# One worker conversation, shared by live and batch modes.
@dataclass
class Job:
    # BOM category being sourced.
    category: str
    # BOM rows in this conversation.
    rows: list[dict]
    # Approved suppliers for the category.
    suppliers: list[dict]
    # Tool definitions sent on every request.
    tools: list[dict]
    # Conversation so far.
    messages: list[dict]
    # Requests answered so far.
    turns: int = 0


def category_suppliers(category: str, suppliers: list[dict]) -> list[dict]:
    """Return the approved suppliers that are searched for this category."""
    # Keep suppliers whose category list includes this category.
    return [s for s in suppliers if category in s["categories"]]


def build_submit_tool(part_ids: list[str], vendor_names: list[str]) -> dict:
    """Build the strict tool the worker calls once with all of its quotes."""
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
            "part_id": {"type": "string", "enum": part_ids},
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


def build_prompt(category: str, rows: list[dict], suppliers: list[dict], currency: str) -> str:
    """Build the task message for one batch."""
    # One line per approved supplier with its domains.
    supplier_lines = "\n".join(f"- {s['name']}: {', '.join(s['domains'])}" for s in suppliers)
    # One JSON line per BOM row.
    row_lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    # Task instructions, suppliers, and BOM rows.
    return f"""Source the "{category}" parts below from the approved suppliers.

Approved suppliers (search and fetch only these):
{supplier_lines}

BOM rows (JSON, one per line):
{row_lines}

For each BOM row:
1. Search each approved supplier once for a listing that matches the description and spec.
2. Fetch a product page only when the search results do not show its price, pack size, and price breaks.
3. For each supplier, keep only its lowest-cost matching listing for the row's quantity.
4. Record the vendor part number, product URL, units per pack, minimum order in packs, and every quantity price break (price per pack, {currency}, US storefront).
5. Only report prices you saw on the supplier's page or search result. Skip listings with no visible price.
6. Note any spec deviation in match_notes.

When done, call {SUBMIT_TOOL} once with every row. Give rows with no valid listing an empty quotes list and a short reason in notes."""


def _domain_allowed(url: str, domains: list[str]) -> bool:
    """Return True if the URL's host is one of the domains or their subdomains."""
    # Lowercase host from the URL.
    host = (urlparse(url).hostname or "").lower()
    # Match the domain itself or any subdomain of it.
    return any(host == d or host.endswith("." + d) for d in domains)


def _check_quote(q: dict, domains_by_vendor: dict[str, list[str]]) -> str | None:
    """Return a rejection reason for an invalid quote, or None if it is valid."""
    # Vendor must be one of this category's suppliers.
    if q["vendor"] not in domains_by_vendor:
        return "vendor not approved for this category"
    # URL must sit on that vendor's approved domains.
    if not _domain_allowed(q["url"], domains_by_vendor[q["vendor"]]):
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
            reason = _check_quote(q, domains_by_vendor)
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


def new_job(category: str, rows: list[dict], suppliers: list[dict], currency: str) -> Job:
    """Set up the tools and first message for one worker conversation."""
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
    # Client tool for the final submission.
    submit = build_submit_tool([r["part_id"] for r in rows], [s["name"] for s in approved])
    # Conversation starts with the batch task.
    first = {"role": "user", "content": build_prompt(category, rows, approved, currency)}
    # The ready-to-send job.
    return Job(category, rows, approved, [web_search, web_fetch, submit], [first])


def request_params(job: Job) -> dict:
    """Return the Messages API parameters for the job's next request."""
    # Same shape for live and batch requests.
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": EFFORT},
        "tools": job.tools,
        "messages": job.messages,
        "cache_control": {"type": "ephemeral"},
    }


def handle_response(job: Job, response, usage: Usage) -> list[PartQuotes] | None:
    """Process one response: return quotes when submitted, None when another turn is needed."""
    # Count this turn.
    job.turns += 1
    # Add this request to the usage totals.
    usage.add(response.usage)
    # Shorthand for this response's usage.
    u = response.usage
    # Log this request's usage.
    log.info(
        "%s [%s]: %s | in %d, cache write %d, cache read %d, out %d",
        job.category, ", ".join(r["part_id"] for r in job.rows), response.stop_reason,
        u.input_tokens or 0, u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0, u.output_tokens or 0,
    )
    # A refusal ends the job.
    if response.stop_reason == "refusal":
        raise WorkerError(f"{job.category}: request refused ({response.id})")
    # Truncated output cannot be trusted.
    if response.stop_reason == "max_tokens":
        raise WorkerError(f"{job.category}: hit max_tokens ({response.id})")
    # Look for the final submission.
    submission = next((b for b in response.content if b.type == "tool_use" and b.name == SUBMIT_TOOL), None)
    # Validate and return the submitted quotes.
    if submission is not None:
        return parse_submission(submission.input, job.rows, job.suppliers)
    # Out of turns without a submission.
    if job.turns >= MAX_TURNS:
        raise WorkerError(f"{job.category}: no submission after {MAX_TURNS} turns")
    # Keep the assistant turn exactly as returned so the next request continues it.
    job.messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})
    # Finished without submitting (not paused): ask for the submission.
    if response.stop_reason != "pause_turn":
        job.messages.append({"role": "user", "content": f"Call {SUBMIT_TOOL} now with everything you found."})
    # Another turn is needed.
    return None


async def source_category(
    client: anthropic.AsyncAnthropic,
    category: str,
    rows: list[dict],
    suppliers: list[dict],
    currency: str = "USD",
    usage: Usage | None = None,
) -> list[PartQuotes]:
    """Live mode: run one worker conversation to completion and return validated quotes."""
    # Local totals when the caller does not pass an accumulator.
    usage = usage if usage is not None else Usage()
    # Conversation state.
    job = new_job(category, rows, suppliers, currency)
    # Loop until the worker submits (handle_response raises when out of turns).
    while True:
        # One streamed Messages API request.
        async with client.messages.stream(**request_params(job)) as stream:
            # Wait for the complete response.
            response = await stream.get_final_message()
        # Quotes, or None when another turn is needed.
        parts = handle_response(job, response, usage)
        # Done once the worker submits.
        if parts is not None:
            return parts
