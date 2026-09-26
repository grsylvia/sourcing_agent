"""Supplier win rates: which approved suppliers win rows in each category, from finished CBOMs."""

# Report rows.
from dataclasses import dataclass

# Multi-run supplier evidence lives in the learning layer.
from ..learning.suppliers import MIN_ROWS_TO_DROP, MIN_RUNS_TO_DROP, supplier_history


# One supplier's record in one category.
@dataclass
class SupplierWins:
    # BOM category.
    category: str
    # Supplier name from suppliers.toml.
    supplier: str
    # Rows this supplier won (lowest total) in the category.
    wins: int
    # Sourced rows in the category across the CBOMs.
    sourced: int

    # Consecutive sufficiently sampled runs with explicit unsuccessful searches.
    failed_runs: int = 0

    @property
    def drop_candidate(self) -> bool:
        """True only after repeated explicit search failures across independent runs."""
        # CBOM win counts alone never establish supplier failure.
        return self.failed_runs >= MIN_RUNS_TO_DROP


def supplier_wins(rows: list[dict], suppliers: list[dict], records: list[dict] | None = None) -> list[SupplierWins]:
    """Count wins per supplier and category for the suppliers in suppliers.toml, sorted by category then wins."""
    # Historical evidence is separate from CBOM winner counts.
    history = supplier_history(records or [])
    # Only sourced rows have a winner.
    sourced = [r for r in rows if r.get("status") == "sourced"]
    # One record per supplier per category it is searched for.
    stats = [
        SupplierWins(
            category,
            s["name"],
            sum(r["category"] == category and r["vendor"] == s["name"] for r in sourced),
            sum(r["category"] == category for r in sourced),
            history.get((category, s["name"], tuple(sorted(s["domains"]))), {}).get("failed_runs", 0),
        )
        for s in suppliers
        for category in s["categories"]
    ]
    # Category, then most wins first.
    return sorted(stats, key=lambda s: (s.category, -s.wins, s.supplier))
