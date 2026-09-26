"""Terminal rendering for sourcing estimates and completed runs; no file writes."""

# Displayed output paths.
from pathlib import Path
# Cost formatting and list prices.
from ..core.pricing import PRICES, Range, estimate_cost, money
# Provider labels.
from ..core.providers import provider_of
# Learned fit descriptions.
from ..learning.regression import describe_fit
# Estimated workload type.
from .estimate import Estimate
# Completed run type.
from .pipeline import RunSummary


def print_estimate(e: Estimate, compare: bool = False) -> None:
    """Print supported run modes and optionally compare both providers fairly."""
    # Report cache reuse before any cost figures.
    print(f"Rows: {e.rows} total | {e.cached} cached (free) | {e.to_source} to source in {e.conversations} worker conversations")
    # Name the chosen provider and model pair.
    print(f"Provider: {provider_of(e.model)} | model: {e.model} | retry: {e.escalation_model}")
    # Cached-only runs need no per-row division.
    if not e.to_source:
        print("Nothing to source: a run costs $0.")
        # Keep model rates visible even when every quote can be reused.
        if compare:
            print_comparison(e)
        # Stop after the zero-cost result.
        return
    # Explain the workload behind the estimate.
    print(f"\n{'Category':<14}{'Rows':>5}{'Suppliers':>11}")
    # One row per BOM category.
    for category, rows, suppliers in e.categories:
        print(f"{category:<14}{rows:>5}{suppliers:>11}")
    # Show the chosen model pair in each supported mode.
    print(f"\n{'Mode':<10}{'First pass':<20}{'Retry / row':<20}Range (no retries → all retried)")
    # Batch and live remain separate price comparisons.
    for batch, label in ((True, "Batch"), (False, "Live")):
        # OpenAI batch web research is not implemented.
        if (batch, e.model) not in e.costs:
            continue
        # First-pass and full-retry bounds.
        first, retry = e.costs[(batch, e.model)], e.costs[(batch, e.escalation_model)]
        # Divide full retry cost across the rows to source.
        per_row = Range(retry.low / e.to_source, retry.high / e.to_source)
        # Worst case includes every first-pass row being retried.
        print(f"{label:<10}{money(first):<20}{money(per_row):<20}{money(Range(first.low, first.high + retry.high))}")
    # State execution constraints alongside the figures.
    print("Anthropic: batch or live. OpenAI: --live required; batch web research is not implemented.")
    # Keep tool fees visible when comparing token discounts.
    print("Batch discounts tokens by 50%; web-search calls cost $0.01 each. --no-escalate skips retries.")
    # Provider-specific learned estimates are separate from the common-workload comparison.
    print_calibration(e)
    # The comparison is opt-in for ordinary estimate users.
    if compare:
        print_comparison(e)


def print_comparison(e: Estimate) -> None:
    """Compare supported runs at list prices using the same assumed token workload."""
    # Show rates and workload totals together.
    print("\nProvider comparison: identical assumed workload, first pass only (USD).")
    # Rates are per million tokens; totals include search fees.
    print(f"{'Provider':<12}{'Model':<22}{'Mode':<8}{'Input':>8}{'Write':>8}{'Read':>8}{'Output':>8}  BOM cost")
    # Each mode represents an executable configuration.
    for (batch, model), cost in e.comparison.items():
        # Current model list rates.
        rates = PRICES[model]
        # Batch discounts every token meter.
        scale = 0.5 if batch else 1.0
        # Keep the mode explicit so live and batch are never conflated.
        mode = "batch" if batch else "live"
        # One comparison row with full meter visibility.
        print(f"{provider_of(model):<12}{model:<22}{mode:<8}{rates.input * scale:>8.3f}{rates.cache_write * scale:>8.3f}{rates.cache_read * scale:>8.3f}{rates.output * scale:>8.3f}  {money(cost)}")
    # Different models may use different numbers of tokens and tools on the same BOM.
    print("Rates per 1M tokens, checked 2026-09-26; standard short context. This is a price comparison, not a measured quality or cost benchmark.")
    # Explain why the two estimates can differ.
    print("Selected-run estimates use that provider's learning history; this table uses shared assumptions for every model.")


def print_calibration(e: Estimate) -> None:
    """Print only the selected provider's learned profile and calibration."""
    # Identify the provider-specific learning pool.
    print(f"Token profile ({provider_of(e.model)}): {e.profile_note}. Details: sourcing learn")
    # Avoid presenting unmeasured OpenAI costs as calibrated Anthropic costs.
    if e.calibration is None:
        print("Calibration: no clean logged passes for this provider yet.")
        # No calibrated values are available.
        return
    # Short description of the provider fit.
    print(f"Calibrated from this provider's logged runs: {describe_fit(e.calibration)}")
    # Preserve the fit uncertainty when enough clean passes exist.
    error = f" ± ${e.calibration.error:.2f}" if e.calibration.error is not None else ""
    # Price only the selected first pass in each available mode.
    for (batch, model), cost in e.costs.items():
        # Retry costs are already shown as per-row ranges.
        if model == e.model:
            print(f"{'Batch' if batch else 'Live'} {model} pass ≈ ${e.calibration.predict(cost.mid):.2f}{error}")


def print_run(summary: RunSummary, out: Path) -> None:
    """Print the run summary: CBOM path, rows by status, parts total, usage, and API cost."""
    # Row counts by status.
    counts = {s: sum(r["status"] == s for r in summary.rows) for s in ("sourced", "not_found", "error")}
    # Total of sourced rows.
    total = sum(float(r["extended_price"]) for r in summary.rows if r["status"] == "sourced")
    # Shorthand for first-pass and escalation usage.
    u, e = summary.usage, summary.escalation_usage
    # Estimated API cost of each pass.
    cost = estimate_cost(u, summary.model, summary.batch)
    esc_cost = estimate_cost(e, summary.escalation_model, summary.batch)
    # Summary lines.
    print(f"CBOM: {out}")
    print(f"Rows: {len(summary.rows)} (sourced {counts['sourced']}, not found {counts['not_found']}, errors {counts['error']}; {summary.reused} reused from cache)")
    print(f"Parts total (sourced rows, excl. shipping): {total:.2f} {summary.currency}")
    print(f"Usage ({summary.model}): {u.requests} requests | input {u.input_tokens:,} | cache write {u.cache_write_tokens:,} | cache read {u.cache_read_tokens:,} | output {u.output_tokens:,} | searches {u.web_searches} | fetches {u.web_fetches}")
    # Escalation usage, only when rows were retried.
    if summary.escalated:
        print(f"Usage ({summary.escalation_model}, {summary.escalated} rows retried): {e.requests} requests | input {e.input_tokens:,} | cache write {e.cache_write_tokens:,} | cache read {e.cache_read_tokens:,} | output {e.output_tokens:,} | searches {e.web_searches} | fetches {e.web_fetches}")
    print(f"Estimated API cost ({'batch' if summary.batch else 'live'}, list price): ${cost + esc_cost:.2f} ({summary.model} ${cost:.2f} + {summary.escalation_model} ${esc_cost:.2f})")
