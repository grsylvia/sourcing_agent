"""CBOM commands: `sourcing estimate` and `sourcing run`."""

# Run the async pipeline.
import asyncio
# File paths.
from pathlib import Path

# Project files.
from ..core import paths
# Personal file locations.
from ..personal.store import bom_path, cbom_path, folder
# Provider defaults and model validation.
from ..core.providers import MODELS
# Estimate without API calls.
from .estimate import estimate_bom
# Sourcing service owns persistence and execution.
from .service import run_sourcing
# Terminal rendering has no persistence side effects.
from .report import print_estimate, print_run


def register(commands) -> None:
    """Add the CBOM subcommands to the CLI."""
    # The run subcommand.
    run = commands.add_parser("run", help="Source a BOM and write a CBOM CSV.")
    run.set_defaults(handler=cmd_run)
    # BOM CSV or .xlsx to source.
    run.add_argument("bom", type=bom_path, help="BOM CSV or .xlsx (see docs/FORMATS.md).")
    # Approved supplier list.
    run.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # CBOM output path.
    run.add_argument("--out", type=cbom_path, help="CBOM CSV path (default: <bom>_cbom.csv in your saved CBOM folder, otherwise home).")
    # How old a cached quote may be and still be reused.
    run.add_argument("--max-age", type=int, default=7, metavar="DAYS", help="Reuse cached quotes up to this many days old; 0 re-sources everything (default: 7).")
    # Opt out of the Batch API for faster, full-price results.
    run.add_argument("--live", action="store_true", help="Run live at full price instead of through the Batch API (50%% off, can take up to 24h).")
    # Skip the stronger-model retry of rows the first pass cannot source.
    run.add_argument("--no-escalate", action="store_true", help="Skip retries of not-found or errored rows on the selected escalation model.")
    # Provider and model selection for a real run.
    add_model_options(run)
    # The estimate subcommand.
    est = commands.add_parser("estimate", help="Estimate the API cost of a run in batch and live mode (no API calls).")
    est.set_defaults(handler=cmd_estimate)
    # BOM CSV or .xlsx to price.
    est.add_argument("bom", type=bom_path, help="BOM CSV or .xlsx (see docs/FORMATS.md).")
    # Approved supplier list.
    est.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Cache age that counts as free reuse.
    est.add_argument("--max-age", type=int, default=7, metavar="DAYS", help="Count cached quotes up to this many days old as free (default: 7).")

    # The same selection controls the run estimate.
    add_model_options(est)
    # Optional provider comparison using identical workload assumptions.
    est.add_argument("--compare", action="store_true", help="Compare supported Anthropic and OpenAI models without API calls.")


def add_model_options(parser) -> None:
    """Expose provider and model choices consistently for run and estimate."""
    # Preserve Anthropic as the default provider.
    parser.add_argument("--provider", choices=MODELS, default="anthropic")
    # A missing model selects the provider default.
    parser.add_argument("--model", help="First-pass model ID for the selected provider.")
    # Retries stay on the selected provider.
    parser.add_argument("--escalation-model", help="Retry model ID (default: Opus 5.5 or GPT-6 Astra).")


def cmd_estimate(args) -> int:
    """Price the BOM without calling the API."""
    # Estimate and print.
    print_estimate(estimate_bom(args.bom, args.suppliers, paths.CACHE_PATH, args.max_age, paths.RUN_LOG_PATH, args.provider, args.model, args.escalation_model), compare=args.compare)
    return 0


def cmd_run(args) -> int:
    """Source the BOM, write the CBOM, and print the summary."""
    # Default output in the current user's chosen CBOM folder.
    out = args.out or folder("cboms") / f"{args.bom.stem}_cbom.csv"
    # Run the whole pipeline.
    summary, lines = asyncio.run(run_sourcing(args.bom, args.suppliers, out, paths.CACHE_PATH, args.max_age, args.live, not args.no_escalate, args.provider, args.model, args.escalation_model, log_path=paths.RUN_LOG_PATH))
    # Summary lines.
    print_run(summary, out)
    # Completed passes were persisted by the service before export.
    for line in lines:
        # Display their pre-run estimated versus actual costs.
        print(line)
    # Show the durable learning location.
    print(f"Learning log: {paths.RUN_LOG_PATH}; report: sourcing learn")
    # Non-zero exit when any row errored.
    return 1 if any(r["status"] == "error" for r in summary.rows) else 0
