"""List prices for estimating run cost (USD; verify at https://platform.claude.com/docs/en/about-claude/pricing)."""

# Typed rate records.
from dataclasses import dataclass

# Usage totals to price.
from .worker import Usage


# Per-million-token rates for one model.
@dataclass(frozen=True)
class Rates:
    # Uncached input.
    input: float
    # Output, including thinking.
    output: float
    # 5-minute prompt-cache writes.
    cache_write: float
    # Prompt-cache reads.
    cache_read: float


# Fee per web search (web fetch has no per-call fee).
SEARCH_PRICE = 10.00 / 1000
# Batch API token discount.
BATCH_DISCOUNT = 0.5

# List rates as of 2026-09.
PRICES = {
    # Newest Opus.
    "claude-opus-5-5": Rates(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20),
    # Previous Opus.
    "claude-opus-5": Rates(input=5.00, output=25.00, cache_write=6.25, cache_read=0.50),
    # Current worker model.
    "claude-sonnet-5": Rates(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20),
    # Cheapest model (older web tools; not a drop-in swap).
    "claude-haiku-4-5": Rates(input=1.00, output=5.00, cache_write=1.25, cache_read=0.10),
}


def estimate_cost(usage: Usage, model: str, batch: bool = False) -> float | None:
    """Return the list-price cost of the usage in USD, or None for an unknown model."""
    # Rates for this model.
    rates = PRICES.get(model)
    # Unknown models cannot be priced.
    if rates is None:
        return None
    # Token cost across all four meters.
    tokens = (
        usage.input_tokens * rates.input
        + usage.output_tokens * rates.output
        + usage.cache_write_tokens * rates.cache_write
        + usage.cache_read_tokens * rates.cache_read
    ) / 1_000_000
    # Batch requests bill tokens at half price.
    if batch:
        tokens *= BATCH_DISCOUNT
    # Tokens plus search fees.
    return tokens + usage.web_searches * SEARCH_PRICE
