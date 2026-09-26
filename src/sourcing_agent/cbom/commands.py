"""CBOM commands: `sourcing estimate` and `sourcing run`."""

# Run the async pipeline.
import asyncio
# File paths.
from pathlib import Path

# Project files.
from ..core import paths
# Models the workers use.
from ..core.agent import ESCALATION_MODEL, MODEL
# Cost model.
from ..core.pricing import Range, estimate_cost, money
# Fitted line and its description.
from ..learning.regression import Fit, describe_fit
# Estimate, calibration, and pass logging.
from .estimate import Estimate, calibrate_sourcing, estimate_bom, log_pass
# Whole-run pipeline.
from .pipeline import RunSummary, run_sourcing


def register(commands) -> None:
    """Add the CBOM subcommands to the CLI."""
    # The run subcommand.
    run = commands.add_parser("run", help="Source a BOM and write a CBOM CSV.")
    run.set_defaults(handler=cmd_run)
    # BOM CSV or .xlsx to source.
    run.add_argument("bom", type=Path, help="BOM CSV or .xlsx (see docs/FORMATS.md).")
    # Approved supplier list.
    run.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # CBOM output path.
    run.add_argument("--out", type=Path, help="CBOM CSV path (default: <bom>_cbom.csv next to the BOM).")
    # How old a cached quote may be and still be reused.
    run.add_argument("--max-age", type=int, default=7, metavar="DAYS", help="Reuse cached quotes up to this many days old; 0 re-sources everything (default: 7).")
    # Opt out of the Batch API for faster, full-price results.
    run.add_argument("--live", action="store_true", help="Run live at full price instead of through the Batch API (50%% off, can take up to 24h).")
    # Skip the stronger-model retry of rows the first pass cannot source.
    run.add_argument("--no-escalate", action="store_true", help=f"Do not retry not-found or errored rows on {ESCALATION_MODEL}.")
    # The estimate subcommand.
    est = commands.add_parser("estimate", help="Estimate the API cost of a run in batch and live mode (no API calls).")
    est.set_defaults(handler=cmd_estimate)
    # BOM CSV or .xlsx to price.
    est.add_argument("bom", type=Path, help="BOM CSV or .xlsx (see docs/FORMATS.md).")
    # Approved supplier list.
    est.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Cache age that counts as free reuse.
    est.add_argument("--max-age", type=int, default=7, metavar="DAYS", help="Count cached quotes up to this many days old as free (default: 7).")


def cmd_estimate(args) -> int:
    """Price the BOM without calling the API."""
    # Estimate and print.
    print_estimate(estimate_bom(args.bom, args.suppliers, paths.CACHE_PATH, args.max_age, paths.RUN_LOG_PATH))
    return 0


def cmd_run(args) -> int:
    """Source the BOM, write the CBOM, and print the summary."""
    # Default output next to the BOM.
    out = args.out or args.bom.with_name(f"{args.bom.stem}_cbom.csv")
    # Run the whole pipeline.
    summary = asyncio.run(run_sourcing(args.bom, args.suppliers, out, paths.CACHE_PATH, args.max_age, args.live, not args.no_escalate))
    # Summary lines.
    print_run(summary, out)
    # Compare with the estimate and log for calibration.
    report_passes(summary, args.bom, calibrate_sourcing(paths.RUN_LOG_PATH))
    # Non-zero exit when any row errored.
    return 1 if any(r["status"] == "error" for r in summary.rows) else 0


def print_estimate(e: Estimate) -> None:
    """Print the pre-run estimate for batch and live mode."""
    # Row counts.
    print(f"Rows: {e.rows} total | {e.cached} cached (free) | {e.to_source} to source in {e.conversations} worker conversations")
    # Nothing to price.
    if not e.to_source:
        print("Nothing to source: a run costs $0.")
        return
    # Rows and suppliers per category (more suppliers means more searches).
    print(f"\n{'Category':<14}{'Rows':>5}{'Suppliers':>11}")
    for category, rows, suppliers in e.categories:
        print(f"{category:<14}{rows:>5}{suppliers:>11}")
    # One line per mode.
    print(f"\n{'Mode':<17}{MODEL + ' pass':<22}{ESCALATION_MODEL + ' retry/row':<27}Range (no retries → all retried)")
    for batch, label in ((True, "Batch (default)"), (False, "Live (--live)")):
        # First-pass range.
        first = e.costs[(batch, MODEL)]
        # Escalation range if every row were retried.
        retry_all = e.costs[(batch, ESCALATION_MODEL)]
        # Escalation cost per retried row.
        per_row = Range(retry_all.low / e.to_source, retry_all.high / e.to_source)
        print(f"{label:<17}{money(first):<22}{money(per_row):<27}{money(Range(first.low, first.high + retry_all.high))}")
    # First-pass difference between modes.
    batch, live = e.costs[(True, MODEL)], e.costs[(False, MODEL)]
    print(f"\nBatch saves {money(Range(live.low - batch.low, live.high - batch.high))} on the first pass (tokens 50% off; $0.01 search fees are not discounted).")
    print("Time: batch usually under 1 h (up to 24 h); live takes minutes.")
    print(f"Retries: only rows {MODEL} cannot source go to {ESCALATION_MODEL}; --no-escalate skips them.")
    # Calibrated figures from logged runs.
    print_calibration(e)


def print_calibration(e: Estimate) -> None:
    """Print estimates corrected by the line fitted to logged actual costs."""
    # No clean runs logged yet.
    if e.calibration is None:
        print("Calibration: no logged runs yet; ranges use assumed token sizes (src/sourcing_agent/cbom/estimate.py).")
        return
    # Shorthand for the fit.
    fit = e.calibration
    # Typical error, when known.
    err = f" ± ${fit.error:.2f}" if fit.error is not None else ""
    # The fitted line.
    print(f"\nCalibrated from logged runs: {describe_fit(fit)}")
    # Calibrated first pass per mode.
    for batch, label in ((True, "Batch (default)"), (False, "Live (--live)")):
        print(f"{label:<17}{MODEL} pass ≈ ${fit.predict(e.costs[(batch, MODEL)].mid):.2f}{err}")


def print_run(summary: RunSummary, out: Path) -> None:
    """Print the run summary: CBOM path, rows by status, parts total, usage, and API cost."""
    # Row counts by status.
    counts = {s: sum(r["status"] == s for r in summary.rows) for s in ("sourced", "not_found", "error")}
    # Total of sourced rows.
    total = sum(float(r["extended_price"]) for r in summary.rows if r["status"] == "sourced")
    # Shorthand for first-pass and escalation usage.
    u, e = summary.usage, summary.escalation_usage
    # Estimated API cost of each pass.
    cost = estimate_cost(u, MODEL, summary.batch)
    esc_cost = estimate_cost(e, ESCALATION_MODEL, summary.batch)
    # Summary lines.
    print(f"CBOM: {out}")
    print(f"Rows: {len(summary.rows)} (sourced {counts['sourced']}, not found {counts['not_found']}, errors {counts['error']}; {summary.reused} reused from cache)")
    print(f"Parts total (sourced rows, excl. shipping): {total:.2f} {summary.currency}")
    print(f"Usage ({MODEL}): {u.requests} requests | input {u.input_tokens:,} | cache write {u.cache_write_tokens:,} | cache read {u.cache_read_tokens:,} | output {u.output_tokens:,} | searches {u.web_searches} | fetches {u.web_fetches}")
    # Escalation usage, only when rows were retried.
    if summary.escalated:
        print(f"Usage ({ESCALATION_MODEL}, {summary.escalated} rows retried): {e.requests} requests | input {e.input_tokens:,} | cache write {e.cache_write_tokens:,} | cache read {e.cache_read_tokens:,} | output {e.output_tokens:,} | searches {e.web_searches} | fetches {e.web_fetches}")
    print(f"Estimated API cost ({'batch' if summary.batch else 'live'}, list price): ${cost + esc_cost:.2f} ({MODEL} ${cost:.2f} + {ESCALATION_MODEL} ${esc_cost:.2f})")


def report_passes(summary: RunSummary, bom: Path, fit: Fit | None) -> None:
    """Log each pass of a run for calibration and print its actual-vs-estimate line."""
    # First pass and escalation pass.
    passes = [
        ("first", MODEL, summary.first_shape, summary.first_errors, summary.usage),
        ("escalation", ESCALATION_MODEL, summary.escalation_shape, summary.escalation_errors, summary.escalation_usage),
    ]
    for name, model, shape, error_rows, usage in passes:
        # Log the pass; print its line when it made requests.
        line = log_pass(paths.RUN_LOG_PATH, name, model, shape, error_rows, usage, summary.batch, bom.name, fit)
        if line:
            print(line)
    # Where the data went.
    print(f"Logged to {paths.RUN_LOG_PATH.name}; the next estimate refits on it.")
