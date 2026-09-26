"""Trials: run the normal sourcing pass on one candidate's domain over sample CBOM rows and compare with the CBOM."""

# Async Claude client.
import anthropic

# Default model and usage totals.
from ..core.agent import MODEL, Usage
# BOM columns the sourcing worker expects.
from ..cbom.bom import BOM_COLUMNS
# Sourcing pass shared with CBOM generation.
from ..cbom.pipeline import source_rows
# Quote records and the price rule.
from ..cbom.quotes import PartQuotes, pick_lowest

# Rows sampled per trial by default.
TRIAL_ROWS = 4


def trial_sample(rows: list[dict], categories: list[str], n: int) -> list[dict]:
    """Up to n CBOM rows in the candidate's categories, unsourced first (gaps), then sourced (price check)."""
    # Rows in the candidate's categories.
    in_cats = [r for r in rows if r["category"] in categories]
    # Gaps first, then sourced rows to compare prices against.
    ordered = [r for r in in_cats if r.get("status") != "sourced"] + [r for r in in_cats if r.get("status") == "sourced"]
    # First n.
    return ordered[:n]


def trial_supplier(domain: str, entry: dict) -> dict:
    """A one-supplier list entry for a trial on the candidate's domain only."""
    # Same shape as a suppliers.toml entry.
    return {"name": entry["name"], "domains": [domain], "categories": entry["categories"]}


async def run_trial(client: anthropic.AsyncAnthropic, sample: list[dict], supplier: dict, currency: str, live: bool, usage: Usage) -> tuple[dict[str, PartQuotes], dict[str, str], list[dict]]:
    """Source the sample rows on the candidate's domain only, with the normal sourcing worker; return quotes, errors, and conversation records."""
    # BOM columns only.
    bom_rows = [{c: r.get(c, "") for c in BOM_COLUMNS} for r in sample]
    # Normal sourcing pass with a one-supplier list.
    return await source_rows(client, bom_rows, {"suppliers": [supplier], "currency": currency}, live, MODEL, usage)


def compare_trial(sample: list[dict], parts: dict[str, PartQuotes], errors: dict[str, str]) -> list[dict]:
    """Per-row outcome of a trial against the CBOM: fills gap, cheaper, costlier, no quote, or error."""
    # One outcome per sampled row.
    outcomes = []
    for r in sample:
        # Trial result for the row.
        part = parts.get(r["part_id"])
        # Cheapest trial order at the BOM quantity.
        pick = pick_lowest(part, int(r["quantity"])) if part else None
        # Current CBOM price, when sourced.
        current = float(r["extended_price"]) if r.get("status") == "sourced" else None
        # Classify the row.
        if r["part_id"] in errors:
            outcome = "error"
        elif pick is None:
            outcome = "no quote"
        elif current is None:
            outcome = "fills gap"
        else:
            outcome = "cheaper" if pick.extended_price < current else "costlier"
        outcomes.append({
            "part_id": r["part_id"],
            "description": r["description"],
            "current_vendor": r.get("vendor", ""),
            "current_price": current,
            "trial_price": round(pick.extended_price, 2) if pick else None,
            "outcome": outcome,
        })
    # Outcomes in sample order.
    return outcomes


def summarize_trial(outcomes: list[dict]) -> dict:
    """Trial totals: rows, quoted, gaps (unsourced rows), gaps filled, cheaper rows, and savings."""
    # Rows the trial beat the CBOM on.
    cheaper = [o for o in outcomes if o["outcome"] == "cheaper"]
    # Totals.
    return {
        "rows": len(outcomes),
        "quoted": sum(o["trial_price"] is not None for o in outcomes),
        "gaps": sum(o["current_price"] is None for o in outcomes),
        "fills": sum(o["outcome"] == "fills gap" for o in outcomes),
        "cheaper": len(cheaper),
        "savings": round(sum(o["current_price"] - o["trial_price"] for o in cheaper), 2),
    }
