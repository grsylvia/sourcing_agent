"""Approved supplier list (suppliers.toml): read by CBOM generation, extended by supplier discovery."""

# Supplier list file.
import tomllib
# File paths.
from pathlib import Path

# Error for bad input files.
from .errors import InputError


def load_suppliers(path: Path) -> dict:
    """Read suppliers.toml and check its required keys."""
    # Read one snapshot for parsing and validation.
    try:
        # Decode supplier configuration consistently across commands.
        content = path.read_text(encoding="utf-8")
    # Preserve the input-error contract for unreadable files.
    except (OSError, UnicodeError) as e:
        raise InputError(f"cannot read {path}: {e}") from e
    # Share validation with startup's source snapshot.
    return parse_suppliers(content, path)


def parse_suppliers(content: str, path: Path) -> dict:
    """Validate an already-read supplier snapshot without reading it again."""
    # Parse the same text the caller will display or hash.
    try:
        # Decode the approved supplier configuration.
        config = tomllib.loads(content)
    # Report malformed TOML through the normal CLI input error.
    except tomllib.TOMLDecodeError as e:
        # Include the selected source for repair.
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
