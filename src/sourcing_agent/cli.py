"""Command line: sourcing run BOM [--suppliers FILE] [--out FILE] [--max-age DAYS] [--live] [--no-escalate]; sourcing estimate BOM."""

# Argument parsing.
import argparse
# Run the async orchestrator.
import asyncio
# Run date for the log.
import datetime
# Progress output.
import logging
# Error output.
import sys
# Usage record for the log.
from dataclasses import asdict
# File paths.
from pathlib import Path

# API error types.
import anthropic

# Run log and fitted calibration line.
from .calibration import Fit, append_run
# Pre-run cost estimate.
from .estimate import Estimate, Range, calibrate, estimate_bom, shape_range
# Orchestrator entry point and input errors.
from .orchestrator import InputError, run_sourcing
# Cost estimate from usage.
from .pricing import estimate_cost
# Models the workers use.
from .worker import ESCALATION_MODEL, MODEL

# Project root folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Approved supplier list at the project root.
DEFAULT_SUPPLIERS = PROJECT_ROOT / "suppliers.toml"
# Quote cache at the project root.
CACHE_PATH = PROJECT_ROOT / "quote_cache.json"
# Actual-vs-estimated cost log at the project root.
RUN_LOG_PATH = PROJECT_ROOT / "run_log.jsonl"


def money(r: Range) -> str:
    """Format a cost range as $low–high."""
    # Two decimals, one dollar sign.
    return f"${r.low:.2f}–{r.high:.2f}"


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


def describe_fit(fit: Fit) -> str:
    """One-line description of a calibration line."""
    # Ratio fit through the origin.
    if fit.ratio:
        return f"actual ≈ {fit.slope:.2f} × estimate midpoint ({fit.n} pass{'es' if fit.n != 1 else ''}; ratio fit until 3 clean passes)"
    # Full regression line.
    r2 = f", R² {fit.r2:.2f}" if fit.r2 is not None else ""
    return f"actual ≈ {fit.slope:.2f} × estimate midpoint {'+' if fit.intercept >= 0 else '-'} ${abs(fit.intercept):.2f} ({fit.n} passes{r2})"


def print_calibration(e: Estimate) -> None:
    """Print estimates corrected by the line fitted to logged actual costs."""
    # No clean runs logged yet.
    if e.calibration is None:
        print("Calibration: no logged runs yet; ranges use assumed token sizes (src/sourcing_agent/estimate.py).")
        return
    # Shorthand for the fit.
    fit = e.calibration
    # Typical error, when known.
    err = f" ± ${fit.error:.2f}" if fit.error is not None else ""
    # The fitted line.
    print(f"\nCalibrated from logged runs: {describe_fit(fit)}")
    # Calibrated first pass per mode.
    for batch, label in ((True, "Batch (default)"), (False, "Live (--live)")):
        r = e.costs[(batch, MODEL)]
        print(f"{label:<17}{MODEL} pass ≈ ${fit.predict((r.low + r.high) / 2):.2f}{err}")


def log_passes(summary, bom: Path, fit: Fit | None) -> None:
    """Print actual vs estimated cost per pass and append each pass to the run log."""
    # First pass and escalation pass, when they called the API.
    passes = [
        ("first", MODEL, summary.first_shape, summary.first_errors, summary.usage),
        ("escalation", ESCALATION_MODEL, summary.escalation_shape, summary.escalation_errors, summary.escalation_usage),
    ]
    for name, model, shape, error_rows, usage in passes:
        # Skip passes that made no requests.
        if not usage.requests:
            continue
        # Assumption-based range for exactly these rows.
        est = shape_range(shape, model, summary.batch)
        # Actual list-price cost of the pass.
        actual = estimate_cost(usage, model, summary.batch)
        # Calibrated prediction from runs logged before this one.
        calibrated = f", calibrated ${fit.predict((est.low + est.high) / 2):.2f}" if fit else ""
        # Errored passes are logged but left out of the fit.
        note = f" ({error_rows} errored rows: excluded from calibration)" if error_rows else ""
        print(f"Actual vs estimate ({model}): ${actual:.2f} vs {money(est)}{calibrated}{note}")
        # One log record per pass.
        append_run(RUN_LOG_PATH, {
            "date": datetime.date.today().isoformat(),
            "bom": bom.name,
            "pass": name,
            "model": model,
            "batch": summary.batch,
            "rows": sum(n for n, _ in shape),
            "shape": shape,
            "error_rows": error_rows,
            "estimate_low": round(est.low, 4),
            "estimate_high": round(est.high, 4),
            "actual_cost": round(actual, 4),
            "usage": asdict(usage),
        })
    # Where the data went.
    print(f"Logged to {RUN_LOG_PATH.name}; the next estimate refits on it.")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run sourcing, and print a summary."""
    # Top-level parser.
    parser = argparse.ArgumentParser(prog="sourcing", description="Source a BOM from approved suppliers.")
    # Subcommands.
    commands = parser.add_subparsers(dest="command", required=True)
    # The run subcommand.
    run = commands.add_parser("run", help="Source a BOM and write a CBOM CSV.")
    # BOM CSV or .xlsx to source.
    run.add_argument("bom", type=Path, help="BOM CSV or .xlsx (see docs/FORMATS.md).")
    # Approved supplier list.
    run.add_argument("--suppliers", type=Path, default=DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
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
    # BOM CSV or .xlsx to price.
    est.add_argument("bom", type=Path, help="BOM CSV or .xlsx (see docs/FORMATS.md).")
    # Approved supplier list.
    est.add_argument("--suppliers", type=Path, default=DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Cache age that counts as free reuse.
    est.add_argument("--max-age", type=int, default=7, metavar="DAYS", help="Count cached quotes up to this many days old as free (default: 7).")
    # Parse the command line.
    args = parser.parse_args(argv)
    # Warnings only from other libraries, on stderr.
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    # Progress lines from this package.
    logging.getLogger("sourcing_agent").setLevel(logging.INFO)
    # Estimate only.
    if args.command == "estimate":
        try:
            # Price the BOM without calling the API.
            print_estimate(estimate_bom(args.bom, args.suppliers, CACHE_PATH, args.max_age, RUN_LOG_PATH))
        except InputError as e:
            # Bad BOM or supplier file.
            print(f"Input error: {e}", file=sys.stderr)
            return 2
        return 0
    # Default output next to the BOM.
    out = args.out or args.bom.with_name(f"{args.bom.stem}_cbom.csv")
    try:
        # Run the whole pipeline.
        summary = asyncio.run(run_sourcing(args.bom, args.suppliers, out, CACHE_PATH, args.max_age, args.live, not args.no_escalate))
    except InputError as e:
        # Bad BOM or supplier file.
        print(f"Input error: {e}", file=sys.stderr)
        return 2
    except anthropic.AuthenticationError:
        # Key present but rejected.
        print("Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    except TypeError as e:
        # No credentials found at all.
        if "authentication" not in str(e):
            raise
        print("No Anthropic credentials found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    # Row counts by status.
    counts = {s: sum(r["status"] == s for r in summary.rows) for s in ("sourced", "not_found", "error")}
    # Total of sourced rows.
    total = sum(float(r["extended_price"]) for r in summary.rows if r["status"] == "sourced")
    # Shorthand for first-pass usage.
    u = summary.usage
    # Shorthand for escalation usage.
    e = summary.escalation_usage
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
    # Compare with the estimate and log for calibration.
    log_passes(summary, args.bom, calibrate(RUN_LOG_PATH))
    # Non-zero exit when any row errored.
    return 1 if counts["error"] else 0


# Allow python -m sourcing_agent.cli.
if __name__ == "__main__":
    sys.exit(main())
