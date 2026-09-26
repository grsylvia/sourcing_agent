"""Quote cache: reuses recent quotes so repeat runs skip rows already sourced."""

# Entry ages.
import datetime
# Stable cache keys.
import hashlib
# Cache file format.
import json
# Quote records to and from JSON.
from dataclasses import asdict
# File paths.
from pathlib import Path

# Quote records.
from .quotes import PartQuotes, PriceBreak, Quote


def quote_key(row: dict, suppliers: list[dict]) -> str:
    """Key a BOM row by what is being bought and where it may be bought (not quantity)."""
    # Fields that define the part, normalized.
    part = [row[c].strip().lower() for c in ("category", "description", "spec", "mfr_part_number")]
    # Supplier names and domains searched for this category.
    where = sorted((s["name"], sorted(s["domains"])) for s in suppliers if row["category"] in s["categories"])
    # Hash of both.
    return hashlib.sha256(json.dumps([part, where]).encode()).hexdigest()[:24]


def load_cache(path: Path) -> dict:
    """Read the cache file, or start empty if it is missing or unreadable."""
    # Missing file means an empty cache.
    if not path.exists():
        return {}
    try:
        # Parse the cache file.
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # A corrupt cache is ignored and rebuilt.
        return {}


def save_cache(path: Path, cache: dict) -> None:
    """Write the cache file."""
    # Readable JSON, one run's worth at a time.
    path.write_text(json.dumps(cache, indent=1))


def lookup(cache: dict, key: str, max_age_days: int, today: datetime.date) -> PartQuotes | None:
    """Return cached quotes no older than max_age_days, else None."""
    # Cached entry, if any.
    entry = cache.get(key)
    # Nothing cached, or reuse disabled.
    if entry is None or max_age_days <= 0:
        return None
    # Too old to reuse.
    if (today - datetime.date.fromisoformat(entry["quoted_at"])).days > max_age_days:
        return None
    # Rebuild the quote records.
    quotes = [Quote(**(q | {"price_breaks": [PriceBreak(**b) for b in q["price_breaks"]]})) for q in entry["quotes"]]
    # Cached result.
    return PartQuotes(entry["part_id"], quotes, entry["notes"], entry["quoted_at"])


def store(cache: dict, key: str, part: PartQuotes) -> None:
    """Save a row's quotes; rows with no quotes are not cached so they are retried."""
    # Only successful rows are worth reusing.
    if part.quotes:
        cache[key] = asdict(part)


def store_parts(cache: dict, keys: dict[str, str], parts: dict[str, PartQuotes], today: datetime.date) -> None:
    """Date completed results and retain their successful quotes immediately."""
    # Empty results remain uncached so later runs can retry them.
    for pid, part in parts.items():
        # Use the sourcing run's date consistently across passes.
        part.quoted_at = today.isoformat()
        # Reuse the cache's successful-quotes-only policy.
        store(cache, keys[pid], part)
