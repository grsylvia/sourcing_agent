"""Command line: sourcing run BOM [--suppliers FILE] [--out FILE] [--max-age DAYS] [--live]."""

# Argument parsing.
import argparse
# Run the async orchestrator.
import asyncio
# Progress output.
import logging
# Error output.
import sys
# File paths.
from pathlib import Path

# API error types.
import anthropic

# Orchestrator entry point and input errors.
from .orchestrator import InputError, run_sourcing
# Cost estimate from usage.
from .pricing import estimate_cost
# Model the workers use.
from .worker import MODEL

# Project root folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Approved supplier list at the project root.
DEFAULT_SUPPLIERS = PROJECT_ROOT / "suppliers.toml"
# Quote cache at the project root.
CACHE_PATH = PROJECT_ROOT / "quote_cache.json"


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
    # Parse the command line.
    args = parser.parse_args(argv)
    # Warnings only from other libraries, on stderr.
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    # Progress lines from this package.
    logging.getLogger("sourcing_agent").setLevel(logging.INFO)
    # Default output next to the BOM.
    out = args.out or args.bom.with_name(f"{args.bom.stem}_cbom.csv")
    try:
        # Run the whole pipeline.
        summary = asyncio.run(run_sourcing(args.bom, args.suppliers, out, CACHE_PATH, args.max_age, args.live))
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
    # Shorthand for usage.
    u = summary.usage
    # Estimated API cost of this run.
    cost = estimate_cost(u, MODEL, summary.batch)
    # Summary lines.
    print(f"CBOM: {out}")
    print(f"Rows: {len(summary.rows)} (sourced {counts['sourced']}, not found {counts['not_found']}, errors {counts['error']}; {summary.reused} reused from cache)")
    print(f"Parts total (sourced rows, excl. shipping): {total:.2f} {summary.currency}")
    print(f"Usage: {u.requests} requests | input {u.input_tokens:,} | cache write {u.cache_write_tokens:,} | cache read {u.cache_read_tokens:,} | output {u.output_tokens:,} | searches {u.web_searches} | fetches {u.web_fetches}")
    print(f"Estimated API cost ({MODEL}, {'batch' if summary.batch else 'live'}, list price): " + (f"${cost:.2f}" if cost is not None else "unknown"))
    # Non-zero exit when any row errored.
    return 1 if counts["error"] else 0


# Allow python -m sourcing_agent.cli.
if __name__ == "__main__":
    sys.exit(main())
