"""Supplier win rates: which approved suppliers win rows in each category, from finished CBOMs."""

# CBOM files.
import csv
# Report rows.
from dataclasses import dataclass
# File paths.
from pathlib import Path

# Error type for bad input files.
from .orchestrator import InputError

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


def read_cboms(paths: list[Path]) -> list[dict]:
    """Read the rows of every CBOM file."""
    # All rows, in file order.
    rows = []
    for path in paths:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # A CBOM needs the columns wins are counted from.
            missing = [c for c in ("category", "status", "vendor") if c not in (reader.fieldnames or [])]
            if missing:
                raise InputError(f"{path}: not a CBOM (missing columns {missing})")
            rows.extend(reader)
    # Combined rows.
    return rows


def supplier_wins(rows: list[dict], suppliers: list[dict]) -> list[SupplierWins]:
    """Count wins per supplier and category for the suppliers in suppliers.toml."""
    # Only sourced rows have a winner.
    sourced = [r for r in rows if r.get("status") == "sourced"]
    # One record per supplier per category it is searched for.
    return [
        SupplierWins(
            category,
            s["name"],
            sum(r["category"] == category and r["vendor"] == s["name"] for r in sourced),
            sum(r["category"] == category for r in sourced),
        )
        for s in suppliers
        for category in s["categories"]
    ]
