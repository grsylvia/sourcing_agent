"""CBOM cost estimate: prices sourcing passes from their shape with a token profile (assumed until learning/ has data) and the calibration fit."""

# Cache ages.
import datetime
# Estimate and learned-state records; summing usage fields.
from dataclasses import dataclass, fields
# File paths.
from pathlib import Path

# Models, turn cap, and usage totals.
from ..core.agent import MAX_TURNS, Usage
# Provider identity, selectable models, and defaults.
from ..core.providers import MODELS, provider_of, select_models
# Supplier list and per-category suppliers.
from ..core.config import category_suppliers, load_suppliers
# Cost model and shared token assumptions.
from ..core.pricing import OUTPUT_PER_ROW, PROMPT_TOKENS, SEARCH_TOKENS, Range, estimate_cost
# Estimator type, per-pass comparison, and the fit over loaded records.
from ..learning.calibration import Estimator, compare_line, fit_records
# Learned token profiles.
from ..learning.profile import POOLED, Profile, learn_profiles, pick_profile
# Fitted line.
from ..learning.regression import Fit
# Run log.
from ..learning.runlog import load_runs, record_pass
# BOM reader.
from .bom import load_bom
# Quote cache reader.
from .cache import load_cache
# Conversation shapes, cache split, and the settings a pass runs with.
from .planning import pass_shape, sourcing_settings, split_cached
# Sourcing worker limits.
from .worker import MAX_PAGE_TOKENS, SEARCHES_PER_PART

# Page tokens per web fetch in the low case (the high case uses the MAX_PAGE_TOKENS cap).
FETCH_TOKENS_LOW = 5_000

# Assumed token profile (the original guesses), used until learning/ has enough logged conversations.
ASSUMED = Profile(
    searches_per_row_supplier=(1.0, float(SEARCHES_PER_PART)),
    fetches_per_row_supplier=(0.0, 1.0),
    search_tokens=(SEARCH_TOKENS, SEARCH_TOKENS),
    fetch_tokens=(FETCH_TOKENS_LOW, MAX_PAGE_TOKENS),
    input_share=(1.0, 1.0),
    rereads=(1.0, 3.0),
    output_per_row=OUTPUT_PER_ROW,
    requests=(1.0, float(MAX_TURNS)),
)


# What learning currently knows, ready for estimates and pass logging.
@dataclass
class Learned:
    # Prices a pass from its shape with the learned (or assumed) profile.
    estimator: Estimator
    # Estimated-vs-actual fit, or None before any clean pass.
    fit: Fit | None
    # Where the token profile comes from, for display.
    profile_note: str


# Pre-run estimate for a BOM.
@dataclass
class Estimate:
    # BOM rows.
    rows: int
    # Rows answered free from the quote cache.
    cached: int
    # Rows that need sourcing.
    to_source: int
    # Worker conversations for those rows.
    conversations: int
    # (category, rows to source, approved suppliers) per category.
    categories: list[tuple[str, int, int]]
    # Cost range keyed by (batch, model); escalation ranges assume every row is retried.
    costs: dict[tuple[bool, str], Range]
    # Line fitted to logged actual costs, or None before any clean run.
    calibration: Fit | None
    # Where the token profile comes from.
    profile_note: str
    # Selected first-pass model.
    model: str
    # Selected retry model.
    escalation_model: str
    # Comparable first-pass prices using one shared assumed workload.
    comparison: dict[tuple[bool, str], Range]


def conversation_usage(n_rows: int, n_suppliers: int, high: bool, profile: Profile) -> Usage:
    """Usage of one sourcing conversation under a token profile, low or high case."""
    # Low or high value of each profile quantity.
    i = int(high)
    # Searches scale with row-supplier pairs, capped by the worker's per-row search limit.
    searches = min(n_rows * n_suppliers * profile.searches_per_row_supplier[i], n_rows * SEARCHES_PER_PART)
    # Page fetches scale the same way.
    fetches = n_rows * n_suppliers * profile.fetches_per_row_supplier[i]
    # Tokens gathered from searches and pages.
    context = searches * profile.search_tokens[i] + fetches * profile.fetch_tokens[i]
    # Requests in the conversation.
    turns = max(1.0, profile.requests[i])
    # Share of gathered context billed as uncached input.
    share = profile.input_share[i]
    # Usage for the conversation.
    return Usage(
        requests=turns,
        input_tokens=context * share,
        cache_write_tokens=PROMPT_TOKENS + context * (1 - share),
        cache_read_tokens=PROMPT_TOKENS * (turns - 1) + context * profile.rereads[i],
        output_tokens=n_rows * profile.output_per_row[i],
        web_searches=searches,
        web_fetches=fetches,
    )


def total_usage(shape: list[tuple[int, int]], high: bool, profile: Profile) -> Usage:
    """Sum usage over sourcing conversations given as (rows, suppliers)."""
    # Running total.
    total = Usage()
    # Add each conversation.
    for n_rows, n_suppliers in shape:
        u = conversation_usage(n_rows, n_suppliers, high, profile)
        for f in fields(Usage):
            setattr(total, f.name, getattr(total, f.name) + getattr(u, f.name))
    # Totals.
    return total


def pass_range(shape: list[tuple[int, int]], model: str, batch: bool, profile: Profile = ASSUMED) -> Range:
    """Cost range for a sourcing pass on one model in one mode under a token profile."""
    # Low and high costs.
    return Range(estimate_cost(total_usage(shape, False, profile), model, batch), estimate_cost(total_usage(shape, True, profile), model, batch))


def sourcing_estimator(records: list[dict]) -> Estimator:
    """Estimator that uses profiles learned from these records (per model, pooled, then assumed)."""
    # Keep pooled token profiles within each provider.
    profiles = {provider: learn_profiles([r for r in records if provider_of(r["model"]) == provider]) for provider in MODELS}
    # Price each pass with its model or provider profile, then assumed constants.
    return lambda shape, model, batch: pass_range(shape, model, batch, pick_profile(profiles[provider_of(model)], model, ASSUMED))


def learned(log_path: Path, provider: str = "anthropic") -> Learned:
    """Current learned state from the run log: estimator, calibration fit, and profile source."""
    # Every logged pass.
    records = [r for r in load_runs(log_path) if provider_of(r["model"]) == provider]
    # Estimator with learned profiles.
    estimator = sourcing_estimator(records)
    # Pooled profile, if learned.
    pooled = learn_profiles(records).get(POOLED)
    note = f"learned from {pooled.n} logged conversations" if pooled else "assumed (fewer than 5 logged conversations)"
    # Learned state.
    return Learned(estimator, fit_records(records, estimator), note)


def estimate_bom(bom_path: Path, suppliers_path: Path, cache_path: Path, max_age_days: int, log_path: Path, provider: str = "anthropic", model: str | None = None, escalation_model: str | None = None) -> Estimate:
    """Estimate the cost of sourcing a BOM in each mode, on each model."""
    # Validate both models before loading files.
    model, escalation_model = select_models(provider, model, escalation_model)
    # Approved suppliers and categories.
    config = load_suppliers(suppliers_path)
    # Checked BOM rows.
    rows = load_bom(bom_path, config["categories"])
    # Rows the cache already answers.
    cached, todo, _ = split_cached(rows, config, load_cache(cache_path), max_age_days, datetime.date.today())
    # Worker conversations a run would start.
    shape = pass_shape(todo, config["suppliers"])
    # What learning knows so far.
    state = learned(log_path, provider)
    # Cost per mode and model.
    costs = {(batch, selected): state.estimator(shape, selected, batch) for batch in (True, False) if not batch or provider == "anthropic" for selected in (model, escalation_model)}
    # Compare list prices on identical assumptions, without cross-provider calibration.
    comparison = {(batch, selected): pass_range(shape, selected, batch) for family, models in MODELS.items() for selected in models for batch in (True, False) if not batch or family == "anthropic"}
    # Rows to source per category, in BOM order.
    counts: dict[str, int] = {}
    for r in todo:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    # Category breakdown with supplier counts.
    categories = [(c, n, len(category_suppliers(c, config["suppliers"]))) for c, n in counts.items()]
    # The estimate.
    return Estimate(len(rows), len(cached), len(todo), len(shape), categories, costs, state.fit, state.profile_note, model, escalation_model, comparison)


def log_pass(log_path: Path, state: Learned, name: str, model: str, batch: bool, shape: list[tuple[int, int]], error_rows: int, usage: Usage, source: str, conversations: list[dict]) -> str | None:
    """Record one sourcing pass (settings, conversations, outcomes) for learning; return its actual-vs-estimate line, or None if it made no requests."""
    # Estimate for exactly these rows, from what was learned before this pass.
    est = state.estimator(shape, model, batch)
    # Append the pass to the run log.
    actual = record_pass(log_path, source, name, model, batch, shape, error_rows, usage, est, sourcing_settings(model, batch), conversations)
    # Comparison line, when the pass made requests.
    return compare_line(model, actual, est, state.fit, error_rows) if actual is not None else None
