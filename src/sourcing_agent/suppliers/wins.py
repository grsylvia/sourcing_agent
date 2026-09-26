"""Supplier win rates: which approved suppliers win rows in each category, from finished CBOMs."""

# Report rows.
from dataclasses import dataclass

# Sourced rows a category needs before a winless supplier is proposed for removal.
MIN_ROWS_TO_DROP = 5


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

    @property
    def drop_candidate(self) -> bool:
        """True when the supplier won nothing over enough sourced rows."""
        # Enough evidence and no wins.
        return self.sourced >= MIN_ROWS_TO_DROP and self.wins == 0


def supplier_wins(rows: list[dict], suppliers: list[dict]) -> list[SupplierWins]:
    """Count wins per supplier and category for the suppliers in suppliers.toml, sorted by category then wins."""
    # Only sourced rows have a winner.
    sourced = [r for r in rows if r.get("status") == "sourced"]
    # One record per supplier per category it is searched for.
    stats = [
        SupplierWins(
            category,
            s["name"],
            sum(r["category"] == category and r["vendor"] == s["name"] for r in sourced),
            sum(r["category"] == category for r in sourced),
        )
        for s in suppliers
        for category in s["categories"]
    ]
    # Category, then most wins first.
    return sorted(stats, key=lambda s: (s.category, -s.wins, s.supplier))
