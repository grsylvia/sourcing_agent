"""Approved supplier list (suppliers.toml): read by CBOM generation, extended by supplier discovery."""

# Supplier list file.
import tomllib
# File paths.
from pathlib import Path

# Error for bad input files.
from .errors import InputError


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


def category_suppliers(category: str, suppliers: list[dict]) -> list[dict]:
    """Return the suppliers that are searched for this category."""
    # Keep suppliers whose category list includes this category.
    return [s for s in suppliers if category in s["categories"]]
