"""Learning command: `sourcing learn`, the run-by-run report for token minimization and cost optimization."""

# Estimator factory type.
from typing import Callable
# File paths.
from pathlib import Path

# Project files.
from ..core import paths
# Estimator type and the fit over loaded records.
from .calibration import Estimator, clean_passes, fit_records
# Token profiles.
from .profile import POOLED, Profile, learn_profiles
# Fit description.
from .regression import describe_fit
# Report sections.
# Supplier reliability from the same learning log.
from .suppliers import supplier_history
# Cost and efficiency reporting.
from .report import by_category, conversation_costs, efficiency, meter_totals, outliers, recommendations
# Run log.
from .runlog import load_runs

# Profile fields shown in the report, with labels.
PROFILE_FIELDS = [
    ("searches_per_row_supplier", "Searches per row per supplier", "{:.2f}"),
    ("fetches_per_row_supplier", "Fetches per row per supplier", "{:.2f}"),
    ("search_tokens", "Tokens per search", "{:,.0f}"),
    ("fetch_tokens", "Tokens per fetch", "{:,.0f}"),
    ("input_share", "Uncached share of context", "{:.0%}"),
    ("rereads", "Context re-reads", "{:.1f}×"),
    ("output_per_row", "Output tokens per row", "{:,.0f}"),
    ("requests", "Requests per conversation", "{:.1f}"),
]


def register(commands, make_estimator: Callable[[list[dict]], Estimator], assumed: Profile) -> None:
    """Add the learn subcommand; the worker's estimator factory and assumed profile are passed in."""
    # The learn subcommand.
    learn = commands.add_parser("learn", help="Report what logged runs teach about tokens and cost, and what to change next (no API calls).")
    learn.set_defaults(handler=lambda args: cmd_learn(args, make_estimator, assumed))
    # Run log to read.
    learn.add_argument("--log", type=Path, default=paths.RUN_LOG_PATH, help="Run log (default: project run_log.jsonl).")


def cmd_learn(args, make_estimator: Callable[[list[dict]], Estimator], assumed: Profile) -> int:
    """Print the learning report."""
    # Every logged pass.
    records = load_runs(args.log)
    # Nothing yet.
    if not records:
        print(f"No passes in {args.log.name} yet: run sourcing once to start learning.")
        return 0
    # Sections.
    convs = conversation_costs(records)
    groups = efficiency(records)
    meters = meter_totals(records)
    categories = by_category(convs)
    worst = outliers(convs)
    print_data(records, convs)
    print_efficiency(groups)
    print_meters(meters)
    print_categories(categories)
    print_profile(learn_profiles(records), assumed)
    print_calibration(records, make_estimator)
    print_outliers(worst)
    print_settings(records)
    # Surface supplier evidence alongside token and cost learning.
    print("\nSupplier outcomes (all passes; failure streaks use first passes only):")
    # Missing historical evidence is unknown, never failure.
    history = supplier_history(records)
    # Show why older data cannot justify pruning.
    if not history:
        # Historical CBOM winners cannot reveal unsuccessful supplier searches.
        print("No explicit supplier outcomes logged yet; no evidence for drops.")
    # Keep the supplier identity visible when domains change.
    for (category, supplier, domains), stats in sorted(history.items()):
        # Display successes, unsuccessful searches, and unknown/error outcomes separately.
        print(f"- {category} / {supplier} ({', '.join(domains)}): {stats['quoted']} quoted, {stats['wins']} wins, {stats['no_quote']} no quote, {stats['error']} errors, {stats['not_checked']} unchecked; {stats['failed_runs']} consecutive failing runs; {'review drop' if stats['drop_candidate'] else 'keep'}")
    # What to change next.
    print("\nRecommendations:")
    for tip in recommendations(records, groups, meters, categories, worst):
        print(f"- {tip}")
    return 0


def print_data(records: list[dict], convs: list[dict]) -> None:
    """What the log holds."""
    # Pass counts.
    clean = len(clean_passes(records))
    untagged = sum(not r.get("conversations") for r in records)
    dates = sorted(r["date"] for r in records)
    print(f"Log: {len(records)} passes ({clean} clean, {len(records) - clean} with errored rows, {untagged} totals-only), {len(convs)} conversations, {dates[0]} to {dates[-1]}")
    print(f"Spend logged: ${sum(r['actual_cost'] for r in records):.2f} list price")


def print_efficiency(groups: list[dict]) -> None:
    """Cost per row and per quoted row by model, mode, and settings."""
    # Table.
    print(f"\n{'Model':<18}{'Mode':<7}{'Settings':<10}{'Passes':>7}{'Rows':>6}{'Cost':>9}{'$/row':>8}{'Coverage':>10}{'$/quoted':>10}")
    for g in groups:
        cov = f"{g['coverage']:.0%}" if g["coverage"] is not None else "-"
        cpq = f"{g['cost_per_quoted']:.3f}" if g["cost_per_quoted"] is not None else "-"
        cpr = f"{g['cost_per_row']:.3f}" if g["cost_per_row"] is not None else "-"
        print(f"{g['model']:<18}{g['mode']:<7}{g['settings_id']:<10}{g['passes']:>7}{g['rows']:>6}{g['cost']:>9.2f}{cpr:>8}{cov:>10}{cpq:>10}")


def print_meters(meters: dict[str, float]) -> None:
    """Share of spend per meter."""
    # Total spend.
    total = sum(meters.values()) or 1.0
    # One line, biggest first.
    parts = [f"{m.replace('_', ' ')} {d / total:.0%} (${d:.2f})" for m, d in sorted(meters.items(), key=lambda kv: -kv[1])]
    print(f"\nWhere the money goes: {' · '.join(parts)}")


def print_categories(categories: list[dict]) -> None:
    """Cost and coverage per category."""
    # Only when conversations were logged.
    if not categories:
        print("\nBy category: no conversation-level data yet (older records hold totals only).")
        return
    print(f"\n{'Category':<13}{'Convs':>6}{'Rows':>6}{'Coverage':>10}{'$/row':>8}{'$/quoted':>10}{'Turns':>7}{'Re-reads':>10}")
    for c in categories:
        cpq = f"{c['cost_per_quoted']:.3f}" if c["cost_per_quoted"] is not None else "-"
        print(f"{c['category']:<13}{c['conversations']:>6}{c['rows']:>6}{c['coverage']:>10.0%}{c['cost_per_row']:>8.3f}{cpq:>10}{c['turns']:>7.1f}{c['rereads']:>9.1f}×")


def print_profile(profiles: dict[str, Profile], assumed: Profile) -> None:
    """Learned token profile next to the assumed one."""
    # Pooled learned profile, if any.
    learned = profiles.get(POOLED)
    # Which models have their own profile.
    own = ", ".join(f"{m} ({p.n})" for m, p in profiles.items() if m != POOLED)
    label = f"learned from {learned.n} conversations" + (f"; per model: {own}" if own else "") if learned else "not learned yet (needs 5 conversations)"
    print(f"\nToken profile (low–high): {label}")
    print(f"{'Quantity':<32}{'Assumed':>22}{'Learned':>22}")
    for name, text, fmt in PROFILE_FIELDS:
        a = getattr(assumed, name)
        a_text = f"{fmt.format(a[0])}–{fmt.format(a[1])}"
        l_text = f"{fmt.format(getattr(learned, name)[0])}–{fmt.format(getattr(learned, name)[1])}" if learned else "-"
        print(f"{text:<32}{a_text:>22}{l_text:>22}")


def print_calibration(records: list[dict], make_estimator: Callable[[list[dict]], Estimator]) -> None:
    """Estimated-vs-actual fit using the current (learned) estimator."""
    # Fit over clean passes.
    fit = fit_records(records, make_estimator(records))
    print(f"\nCalibration: {describe_fit(fit) if fit else 'no clean passes yet'}")


def print_outliers(worst: list[dict]) -> None:
    """Costliest conversations."""
    # Only when conversations were logged.
    if not worst:
        return
    print("\nCostliest conversations:")
    for c in worst:
        u = c["usage"]
        times = f", {c['times_median']:.1f}× median" if c["times_median"] else ""
        print(f"- {c['model']} {c['label']}: ${c['cost']:.2f}{times} | {c['turns']} turns, {u['web_searches']} searches, {u['web_fetches']} fetches, {u['cache_read_tokens']:,} cache reads, {c['quoted_rows']}/{c['rows']} quoted")


def print_settings(records: list[dict]) -> None:
    """What each settings ID means."""
    # Distinct tagged settings.
    seen = {r["settings_id"]: r["settings"] for r in records if r.get("settings_id")}
    if not seen:
        return
    print("\nSettings IDs:")
    for sid, settings in seen.items():
        print(f"- {sid}: " + ", ".join(f"{k}={v}" for k, v in settings.items() if k not in ("model", "batch")))
