"""Sourcing application boundary: input files, provider lifecycle, learning, and exports."""

# Cache planning date.
import datetime
# File arguments remain explicit for callers and tests.
from pathlib import Path
# Provider validation and client creation.
from ..core.providers import create_client, select_models, validate_mode
# Supplier configuration.
from ..core.config import load_suppliers
# Default personal learning location.
from ..core import paths
# BOM input and CBOM output.
from .bom import load_bom, write_cbom
# Local quote storage.
from .cache import load_cache, save_cache
# Freeze learned estimates before starting paid work.
from .estimate import learned, log_pass
# Completed-pass facts.
from .execution import SourcingPass
# In-memory orchestration.
from .pipeline import RunSummary, source_bom
# Decide whether any API client is needed.
from .planning import split_cached


def run_observer(log_path: Path, provider: str, source: str, batch: bool):
    """Freeze prior learning and return a completed-pass recorder plus report lines."""
    # A run must be compared with what was known before it began.
    state = learned(log_path, provider)
    # Accumulate presentation text without printing from the service.
    lines = []

    def record(completed: SourcingPass) -> None:
        """Persist pass facts before the next pass or output write."""
        # Log metered work immediately using the original estimate.
        line = log_pass(log_path, state, completed.name, completed.model, batch, completed.shape, completed.error_rows, completed.usage, source, completed.conversations)
        # Cache-only passes make no requests and need no cost line.
        if line:
            # The CLI can render this after successful export.
            lines.append(line)

    # Keep recording separate from rendering.
    return record, lines


async def run_sourcing(
    bom_path: Path,
    suppliers_path: Path,
    out_path: Path,
    cache_path: Path,
    max_age_days: int = 7,
    live: bool = False,
    escalate: bool = True,
    provider: str = "anthropic",
    model: str | None = None,
    escalation_model: str | None = None,
    *,
    log_path: Path | None = None,
) -> tuple[RunSummary, list[str]]:
    """Source and export a BOM; preserve completed-pass learning if export fails."""
    # Reject incompatible model selections before paid work.
    model, escalation_model = select_models(provider, model, escalation_model)
    # Preserve explicit provider/mode behavior, even for cached runs.
    validate_mode(provider, live)
    # Read the approved supplier configuration.
    config = load_suppliers(suppliers_path)
    # Validate all input rows before creating a provider client.
    rows = load_bom(bom_path, config["categories"])
    # Load this user's saved valid quotes.
    cache = load_cache(cache_path)
    # A cache-only run requires no credentials or network client.
    _, todo, _ = split_cached(rows, config, cache, max_age_days, datetime.date.today())
    # Capture the pre-run learning state and completed-pass observer.
    record, lines = run_observer(log_path if log_path is not None else paths.RUN_LOG_PATH, provider, bom_path.name, not live)
    # Cache-only processing remains identical to a paid run's price selection.
    if not todo:
        # None is safe because no worker executes for fully cached inputs.
        summary = await source_bom(None, rows, config, cache, max_age_days, live, escalate, model, escalation_model, record)
    # Create a provider client only when fresh sourcing is needed.
    else:
        # Share one client among category workers.
        async with create_client(provider) as client:
            # Retain cached successful results even if later work fails.
            try:
                # Each completed pass triggers durable learning immediately.
                summary = await source_bom(client, rows, config, cache, max_age_days, live, escalate, model, escalation_model, record)
            # Persist available quote state independently of the CBOM export.
            finally:
                # A later export failure cannot discard reusable prices.
                save_cache(cache_path, cache)
    # Export after paid-pass learning has been persisted.
    write_cbom(out_path, summary.rows)
    # Return facts and display text separately.
    return summary, lines
