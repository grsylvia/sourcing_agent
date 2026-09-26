"""Candidate registry (supplier_candidates.json): every discovered domain, its screening, trial, and status; approvals extend suppliers.toml."""

# Dates for entries.
import datetime
# Registry file and TOML strings.
import json
# Candidate records to dicts.
from dataclasses import asdict
# File paths.
from pathlib import Path

# Supplier list reader.
from ..core.config import load_suppliers
# Error for refused actions.
from ..core.errors import InputError
# Domain helper.
from ..core.web import host_of
# Candidate record.
from .scout import Candidate, known_domains

# Status of each registry entry.
STATUSES = ("proposed", "screened_out", "trialed", "approved", "rejected")


def load_registry(path: Path) -> dict:
    """Read the candidate registry, or start empty."""
    # Missing file means no candidates yet.
    if not path.exists():
        return {}
    # Domain -> entry.
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(path: Path, registry: dict) -> None:
    """Write the candidate registry."""
    # Readable JSON, sorted by domain.
    path.write_text(json.dumps(dict(sorted(registry.items())), indent=1), encoding="utf-8")


def entry_for(registry: dict, domain: str) -> tuple[str, dict]:
    """Return the normalized domain and its entry, or raise if it was never discovered."""
    # Normalized domain.
    domain = host_of(domain)
    # Unknown domains cannot be trialed, approved, or rejected.
    if domain not in registry:
        raise InputError(f"{domain} is not in the registry; run sourcing discover first")
    # Domain and entry.
    return domain, registry[domain]


def record_candidates(registry: dict, candidates: list[Candidate], today: datetime.date) -> None:
    """Add screened candidates to the registry as proposed or screened_out."""
    for c in candidates:
        # Everything the scout reported plus the screening, keyed by domain.
        entry = asdict(c)
        entry.update({
            "categories": [entry.pop("category")],
            "status": "proposed" if c.verdict == "trial" else "screened_out",
            "found": today.isoformat(),
            "updated": today.isoformat(),
        })
        registry[c.domain] = entry


def record_trial(entry: dict, summary: dict, outcomes: list[dict], today: datetime.date) -> None:
    """Store a trial's totals and row outcomes on the entry and mark it trialed."""
    # Trial record.
    entry["trial"] = {"date": today.isoformat(), **summary, "outcomes": outcomes}
    # New status.
    entry.update({"status": "trialed", "updated": today.isoformat()})


def supplier_block(domain: str, entry: dict) -> str:
    """suppliers.toml text for an approved candidate, commented like the rest of the file."""
    # Trial summary for the header comment.
    trial = entry.get("trial") or {}
    summary = f"trial {trial.get('quoted', 0)}/{trial.get('rows', 0)} rows quoted, {trial.get('fills', 0)} gaps filled, {trial.get('cheaper', 0)} cheaper" if trial else "no trial"
    # Header comment, then one comment above each setting.
    return (
        f"\n# {entry['name']}: found by supplier discovery on {entry['found']}; {summary}.\n"
        "[[suppliers]]\n"
        "# Name written to the CBOM vendor column.\n"
        f"name = {json.dumps(entry['name'])}\n"
        "# Domains the worker may search and fetch.\n"
        f"domains = {json.dumps([domain])}\n"
        "# Categories this supplier is searched for.\n"
        f"categories = {json.dumps(entry['categories'])}\n"
    )


def approve(registry: dict, domain: str, suppliers_path: Path, without_trial: bool, today: datetime.date) -> tuple[str, dict]:
    """Append a trialed candidate to suppliers.toml and mark it approved; return its domain and entry."""
    # Candidate entry.
    domain, entry = entry_for(registry, domain)
    # Verification gate: a trial first, unless explicitly waived.
    if entry["status"] != "trialed" and not without_trial:
        raise InputError(f"{domain} is {entry['status']}; trial it first (sourcing trial {domain} --cbom …) or pass --without-trial")
    # Current supplier file.
    original = suppliers_path.read_text(encoding="utf-8")
    # Never add a domain twice.
    if domain in known_domains(load_suppliers(suppliers_path)["suppliers"], {}):
        raise InputError(f"{domain} is already in {suppliers_path.name}")
    # Append, then make sure the file still loads; restore it if not.
    suppliers_path.write_text(original.rstrip("\n") + "\n" + supplier_block(domain, entry), encoding="utf-8")
    try:
        load_suppliers(suppliers_path)
    except InputError:
        suppliers_path.write_text(original, encoding="utf-8")
        raise
    # Record the approval.
    entry.update({"status": "approved", "updated": today.isoformat()})
    return domain, entry


def reject(registry: dict, domain: str, today: datetime.date) -> str:
    """Mark a candidate rejected so discovery skips it; return its domain."""
    # Candidate entry.
    domain, entry = entry_for(registry, domain)
    # New status.
    entry.update({"status": "rejected", "updated": today.isoformat()})
    return domain
