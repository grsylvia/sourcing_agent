"""Quote records and the price rule: lowest total at the BOM quantity (price breaks, pack size, MOQ; shipping excluded)."""

# Round pack counts up.
import math
# Typed records.
from dataclasses import dataclass, field


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
