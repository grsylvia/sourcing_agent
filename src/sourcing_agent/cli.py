"""Command line: sourcing run BOM [--suppliers FILE] [--out FILE] [--max-age DAYS] [--live] [--no-escalate]; sourcing estimate BOM; sourcing suppliers CBOM...; sourcing discover CBOM...; sourcing trial DOMAIN --cbom CBOM...; sourcing candidates."""

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
# Supplier discovery, screening, trials, and the candidate registry.
from .discover import (
    TRIAL_ROWS, Candidate, compare_trial, example_rows, gap_rows, host_of, known_domains, load_registry, rdap_age_years,
    record_candidates, run_scouts, run_trial, save_registry, scout_estimate, scout_job, screen, seed_domains,
    supplier_block, trial_sample, trial_supplier,
)
# Pre-run cost estimate.
from .estimate import Estimate, Range, calibrate, estimate_bom, shape_range
# Orchestrator entry point, supplier loader, conversation shapes, and input errors.
from .orchestrator import InputError, load_suppliers, pass_shape, run_sourcing
# Supplier win rates from CBOMs.
from .wins import MIN_ROWS_TO_DROP, read_cboms, supplier_wins
# Cost estimate from usage.
from .pricing import estimate_cost
# Models the workers use and usage totals.
from .worker import ESCALATION_MODEL, MODEL, Usage

# Project root folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Approved supplier list at the project root.
DEFAULT_SUPPLIERS = PROJECT_ROOT / "suppliers.toml"
# Quote cache at the project root.
CACHE_PATH = PROJECT_ROOT / "quote_cache.json"
# Actual-vs-estimated cost log at the project root.
RUN_LOG_PATH = PROJECT_ROOT / "run_log.jsonl"
# Discovered-supplier registry at the project root.
REGISTRY_PATH = PROJECT_ROOT / "supplier_candidates.json"


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


def log_pass(name: str, model: str, shape: list[tuple[int, int]], error_rows: int, usage: Usage, batch: bool, source: str, fit: Fit | None) -> None:
    """Print actual vs estimated cost for one sourcing pass and append it to the run log."""
    # Skip passes that made no requests.
    if not usage.requests:
        return
    # Assumption-based range for exactly these rows.
    est = shape_range(shape, model, batch)
    # Actual list-price cost of the pass.
    actual = estimate_cost(usage, model, batch)
    # Calibrated prediction from runs logged before this one.
    calibrated = f", calibrated ${fit.predict((est.low + est.high) / 2):.2f}" if fit else ""
    # Errored passes are logged but left out of the fit.
    note = f" ({error_rows} errored rows: excluded from calibration)" if error_rows else ""
    print(f"Actual vs estimate ({model}): ${actual:.2f} vs {money(est)}{calibrated}{note}")
    # One log record per pass.
    append_run(RUN_LOG_PATH, {
        "date": datetime.date.today().isoformat(),
        "bom": source,
        "pass": name,
        "model": model,
        "batch": batch,
        "rows": sum(n for n, _ in shape),
        "shape": shape,
        "error_rows": error_rows,
        "estimate_low": round(est.low, 4),
        "estimate_high": round(est.high, 4),
        "actual_cost": round(actual, 4),
        "usage": asdict(usage),
    })


def log_passes(summary, bom: Path, fit: Fit | None) -> None:
    """Print actual vs estimated cost per pass of a run and append each pass to the run log."""
    # First pass and escalation pass.
    log_pass("first", MODEL, summary.first_shape, summary.first_errors, summary.usage, summary.batch, bom.name, fit)
    log_pass("escalation", ESCALATION_MODEL, summary.escalation_shape, summary.escalation_errors, summary.escalation_usage, summary.batch, bom.name, fit)
    # Where the data went.
    print(f"Logged to {RUN_LOG_PATH.name}; the next estimate refits on it.")


def run_api(coro) -> tuple[object, int | None]:
    """Run an async API job; return (result, None), or (None, 2) after printing a credentials error."""
    try:
        # Run to completion.
        return asyncio.run(coro), None
    except anthropic.AuthenticationError:
        # Key present but rejected.
        print("Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return None, 2
    except TypeError as e:
        # No credentials found at all.
        if "authentication" not in str(e):
            raise
        print("No Anthropic credentials found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return None, 2


async def scout(jobs: list, live: bool, usage: Usage) -> list:
    """Run scout jobs with a fresh client."""
    # One client for all scouts.
    async with anthropic.AsyncAnthropic() as client:
        return await run_scouts(client, jobs, live, usage)


async def trial(sample: list[dict], supplier: dict, currency: str, live: bool, usage: Usage) -> tuple[dict, dict]:
    """Run one trial with a fresh client."""
    # One client for the trial pass.
    async with anthropic.AsyncAnthropic() as client:
        return await run_trial(client, sample, supplier, currency, live, usage)


def print_candidates(candidates: list[Candidate]) -> None:
    """Print the screening scorecard for new candidates."""
    # Nothing found.
    if not candidates:
        print("No new candidates found.")
        return
    # One line per candidate.
    print(f"\n{'Category':<13}{'Store (domain)':<42}{'Verdict':<14}{'Evidence':>9}{'Age':>7}  Flags")
    for c in candidates:
        age = f"{c.domain_age_years:.0f}y" if c.domain_age_years is not None else "?"
        store = f"{c.name} ({c.domain})"[:40]
        print(f"{c.category:<13}{store:<42}{c.verdict:<14}{c.evidence:>9}{age:>7}  {'; '.join(c.reasons) or '-'}")
    # Why each one might help.
    print("\nWhy the scout proposed them:")
    for c in candidates:
        print(f"- {c.domain}: {c.reason}")


def cmd_discover(args) -> int:
    """Scout categories with unsourced rows, screen the candidates for free, and save them to the registry."""
    # Approved suppliers, CBOM rows, and domains already dealt with.
    config = load_suppliers(args.suppliers)
    rows = read_cboms(args.cboms)
    registry = load_registry(REGISTRY_PATH)
    known = known_domains(config["suppliers"], registry)
    # Unsourced rows per category.
    gaps = gap_rows(rows)
    # Requested categories, or those with gaps (most gaps first).
    categories = args.category or sorted(gaps, key=lambda c: -len(gaps[c]))
    # Categories must exist in suppliers.toml.
    unknown = [c for c in categories if c not in config["categories"]]
    if unknown:
        raise InputError(f"unknown categories {unknown}")
    # Nothing to scout.
    if not categories:
        print("No not_found or error rows in these CBOMs; pass --category to scout a category anyway.")
        return 0
    # Free leads from URLs in BOM notes.
    seeds = seed_domains(rows, known)
    # Mode and estimate.
    batch = not args.live
    est = scout_estimate(len(categories), batch)
    # Plan.
    print("Scout plan (one conversation per category):")
    for c in categories:
        print(f"- {c}: {len(gaps.get(c, []))} unsourced rows")
    print(f"Free leads from BOM notes: {', '.join(seeds) or 'none'}")
    print(f"Estimated cost ({MODEL}, uncalibrated): batch {money(scout_estimate(len(categories), True))}, live {money(scout_estimate(len(categories), False))}")
    # Estimate only.
    if args.estimate:
        return 0
    # One scout per category.
    jobs = [scout_job(c, example_rows(rows, c), seeds, known) for c in categories]
    usage = Usage()
    outcomes, code = run_api(scout(jobs, args.live, usage))
    if code:
        return code
    # Candidates from every scout that finished.
    candidates: list[Candidate] = []
    for category, outcome in zip(categories, outcomes):
        if isinstance(outcome, str):
            print(f"{category}: scout failed: {outcome}", file=sys.stderr)
            continue
        candidates.extend(outcome)
    # Free screening, including RDAP domain age.
    today = datetime.date.today()
    for c in candidates:
        screen(c, rdap_age_years(c.domain, today))
    # Save to the registry so no domain is paid for twice.
    record_candidates(registry, candidates, today)
    save_registry(REGISTRY_PATH, registry)
    # Scorecard and cost.
    print_candidates(candidates)
    print(f"\nActual cost ({'batch' if batch else 'live'}): ${estimate_cost(usage, MODEL, batch):.2f} vs estimate {money(est)}")
    print(f"Saved to {REGISTRY_PATH.name}. Next: sourcing trial <domain> --cbom <cbom.csv>")
    # Non-zero exit when a scout failed.
    return 1 if any(isinstance(o, str) for o in outcomes) else 0


def cmd_trial(args) -> int:
    """Quote sample CBOM rows on one candidate's domain only and compare with the CBOM."""
    # Candidate from the registry.
    registry = load_registry(REGISTRY_PATH)
    domain = host_of(args.domain)
    entry = registry.get(domain)
    if entry is None:
        raise InputError(f"{domain} is not in {REGISTRY_PATH.name}; run sourcing discover first")
    # Screened-out candidates can still be trialed, with a warning.
    if entry["status"] == "screened_out":
        print(f"Warning: {domain} was screened out ({'; '.join(entry['reasons'])}).")
    # Sample rows and the one-supplier list.
    config = load_suppliers(args.suppliers)
    sample = trial_sample(read_cboms(args.cbom), entry["categories"], args.rows)
    if not sample:
        print(f"No CBOM rows in {entry['categories']}; nothing to trial.")
        return 0
    supplier = trial_supplier(domain, entry)
    shape = pass_shape(sample, [supplier])
    # Mode, estimate, and calibration.
    batch = not args.live
    est = shape_range(shape, MODEL, batch)
    fit = calibrate(RUN_LOG_PATH)
    gaps = sum(r.get("status") != "sourced" for r in sample)
    # Plan.
    print(f"Trial of {entry['name']} ({domain}) on {len(sample)} rows ({gaps} unsourced, {len(sample) - gaps} sourced for a price check)")
    calibrated = f", calibrated ${fit.predict((est.low + est.high) / 2):.2f}" if fit else ""
    print(f"Estimated cost ({'batch' if batch else 'live'}): {money(est)}{calibrated}")
    # Estimate only.
    if args.estimate:
        return 0
    # Normal sourcing pass on the candidate's domain only.
    usage = Usage()
    result, code = run_api(trial(sample, supplier, config["currency"], args.live, usage))
    if code:
        return code
    parts, errors = result
    # Row-by-row comparison with the CBOM.
    outcomes = compare_trial(sample, parts, errors)
    print(f"\n{'Part':<10}{'Outcome':<11}{'Trial':>10}{'Current':>10}  Current vendor / description")
    for o in outcomes:
        trial_price = f"${o['trial_price']:.2f}" if o["trial_price"] is not None else "-"
        current = f"${o['current_price']:.2f}" if o["current_price"] is not None else "-"
        print(f"{o['part_id']:<10}{o['outcome']:<11}{trial_price:>10}{current:>10}  {o['current_vendor'] or '-'} / {o['description']}")
    # Trial summary.
    quoted = sum(o["trial_price"] is not None for o in outcomes)
    fills = sum(o["outcome"] == "fills gap" for o in outcomes)
    cheaper = [o for o in outcomes if o["outcome"] == "cheaper"]
    savings = sum(o["current_price"] - o["trial_price"] for o in cheaper)
    print(f"\nQuoted {quoted}/{len(outcomes)} rows | gaps filled {fills}/{gaps} | cheaper on {len(cheaper)} rows (saves ${savings:.2f})")
    # Cost vs estimate, logged for calibration (same worker as sourcing).
    log_pass("trial", MODEL, shape, len(errors), usage, batch, f"trial {domain}", fit)
    # Record the trial.
    today = datetime.date.today().isoformat()
    entry["trial"] = {"date": today, "rows": len(outcomes), "quoted": quoted, "fills": fills, "cheaper": len(cheaper), "savings": round(savings, 2), "outcomes": outcomes}
    entry.update({"status": "trialed", "updated": today})
    save_registry(REGISTRY_PATH, registry)
    print(f"Saved to {REGISTRY_PATH.name}. Approve with: sourcing candidates --approve {domain}")
    return 0


def cmd_candidates(args) -> int:
    """List discovered suppliers, or approve (add to suppliers.toml) or reject one."""
    # Registry of discovered domains.
    registry = load_registry(REGISTRY_PATH)
    # Approve: append a commented [[suppliers]] entry.
    if args.approve:
        domain = host_of(args.approve)
        entry = registry.get(domain)
        if entry is None:
            raise InputError(f"{domain} is not in {REGISTRY_PATH.name}")
        # Verification gate: a trial first, unless explicitly waived.
        if entry["status"] != "trialed" and not args.without_trial:
            raise InputError(f"{domain} is {entry['status']}; trial it first (sourcing trial {domain} --cbom …) or pass --without-trial")
        # Current supplier file and its domains.
        original = args.suppliers.read_text(encoding="utf-8")
        if domain in known_domains(load_suppliers(args.suppliers)["suppliers"], {}):
            raise InputError(f"{domain} is already in {args.suppliers.name}")
        # Append, then make sure the file still loads; restore it if not.
        args.suppliers.write_text(original.rstrip("\n") + "\n" + supplier_block(domain, entry), encoding="utf-8")
        try:
            load_suppliers(args.suppliers)
        except InputError:
            args.suppliers.write_text(original, encoding="utf-8")
            raise
        # Record the approval.
        entry.update({"status": "approved", "updated": datetime.date.today().isoformat()})
        save_registry(REGISTRY_PATH, registry)
        print(f"Added {entry['name']} ({domain}) to {args.suppliers.name} for {', '.join(entry['categories'])}.")
        print("Note: cached quotes for those categories are re-sourced on the next run (the supplier list is part of the cache key).")
        return 0
    # Reject: discovery will skip the domain from now on.
    if args.reject:
        domain = host_of(args.reject)
        if domain not in registry:
            raise InputError(f"{domain} is not in {REGISTRY_PATH.name}")
        registry[domain].update({"status": "rejected", "updated": datetime.date.today().isoformat()})
        save_registry(REGISTRY_PATH, registry)
        print(f"Rejected {domain}; discovery will skip it.")
        return 0
    # List everything.
    if not registry:
        print("No discovered suppliers yet. Run: sourcing discover <cbom.csv>")
        return 0
    print(f"{'Domain':<28}{'Name':<26}{'Categories':<22}{'Status':<14}Trial")
    for domain, e in registry.items():
        t = e.get("trial")
        trial_text = f"{t['quoted']}/{t['rows']} quoted, {t['fills']} gaps filled, {t['cheaper']} cheaper (${t['savings']:.2f})" if t else "-"
        print(f"{domain:<28}{e['name'][:24]:<26}{', '.join(e['categories'])[:20]:<22}{e['status']:<14}{trial_text}")
    return 0


def print_wins(cboms: list[Path], suppliers_path: Path) -> None:
    """Print supplier win rates per category and the suppliers that never win."""
    # Win counts for every supplier and category in suppliers.toml.
    stats = supplier_wins(read_cboms(cboms), load_suppliers(suppliers_path)["suppliers"])
    # Category, then most wins first.
    stats.sort(key=lambda s: (s.category, -s.wins, s.supplier))
    # Table of wins.
    print(f"{'Category':<14}{'Supplier':<16}{'Wins':>5}{'Share':>7}{'Sourced rows':>14}")
    for s in stats:
        share = f"{s.wins / s.sourced:.0%}" if s.sourced else "-"
        print(f"{s.category:<14}{s.supplier:<16}{s.wins:>5}{share:>7}{s.sourced:>14}")
    # Suppliers that won nothing over enough rows.
    drops = [s for s in stats if s.drop_candidate]
    if not drops:
        print(f"\nNo drop candidates (a supplier needs 0 wins over at least {MIN_ROWS_TO_DROP} sourced rows in a category).")
        return
    print(f"\nDrop candidates (0 wins over at least {MIN_ROWS_TO_DROP} sourced rows):")
    for s in drops:
        print(f"- {s.supplier} from {s.category} ({s.sourced} rows)")
    print("Each drop saves about one search ($0.01) plus its result tokens per future row in that category.")
    print("Dropping changes that category's quote-cache key, so its cached quotes are re-sourced on the next run.")


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
    # The suppliers subcommand.
    sup = commands.add_parser("suppliers", help="Show supplier win rates per category from CBOMs and suppliers that never win.")
    # Finished CBOMs to count wins in.
    sup.add_argument("cboms", type=Path, nargs="+", help="One or more CBOM CSVs.")
    # Approved supplier list.
    sup.add_argument("--suppliers", type=Path, default=DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # The discover subcommand.
    dis = commands.add_parser("discover", help="Scout the web for new suppliers where CBOM rows went unsourced, and screen them for free.")
    # CBOMs whose gaps pick the categories and example parts.
    dis.add_argument("cboms", type=Path, nargs="+", help="One or more CBOM CSVs.")
    # Categories to scout instead of the ones with gaps.
    dis.add_argument("--category", action="append", help="Scout this category (repeatable); default: categories with not_found/error rows.")
    # Approved supplier list.
    dis.add_argument("--suppliers", type=Path, default=DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Opt out of the Batch API.
    dis.add_argument("--live", action="store_true", help="Run live at full price instead of through the Batch API.")
    # Plan and estimate without API calls.
    dis.add_argument("--estimate", action="store_true", help="Print the plan and cost estimate, then stop (no API calls).")
    # The trial subcommand.
    tri = commands.add_parser("trial", help="Quote sample CBOM rows on one candidate's domain only and compare with the CBOM.")
    # Candidate to trial.
    tri.add_argument("domain", help="Candidate domain from sourcing candidates.")
    # CBOMs to sample rows from.
    tri.add_argument("--cbom", type=Path, nargs="+", required=True, help="CBOM CSVs to sample rows from.")
    # Sample size.
    tri.add_argument("--rows", type=int, default=TRIAL_ROWS, help=f"Rows to sample, unsourced first (default: {TRIAL_ROWS}).")
    # Approved supplier list (currency).
    tri.add_argument("--suppliers", type=Path, default=DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Opt out of the Batch API.
    tri.add_argument("--live", action="store_true", help="Run live at full price instead of through the Batch API.")
    # Plan and estimate without API calls.
    tri.add_argument("--estimate", action="store_true", help="Print the plan and cost estimate, then stop (no API calls).")
    # The candidates subcommand.
    can = commands.add_parser("candidates", help="List discovered suppliers, or approve or reject one.")
    # Approve or reject, not both.
    decide = can.add_mutually_exclusive_group()
    decide.add_argument("--approve", metavar="DOMAIN", help="Add a trialed candidate to suppliers.toml.")
    decide.add_argument("--reject", metavar="DOMAIN", help="Mark a candidate rejected so discovery skips it.")
    # Waive the trial requirement.
    can.add_argument("--without-trial", action="store_true", help="Allow --approve before a trial.")
    # Supplier list to add approved candidates to.
    can.add_argument("--suppliers", type=Path, default=DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
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
    # Win-rate report only.
    if args.command == "suppliers":
        try:
            # Count wins in the CBOMs.
            print_wins(args.cboms, args.suppliers)
        except (InputError, OSError, KeyError) as e:
            # Missing file, bad supplier file, or not a CBOM.
            print(f"Input error: {e}", file=sys.stderr)
            return 2
        return 0
    # Supplier discovery commands.
    handlers = {"discover": cmd_discover, "trial": cmd_trial, "candidates": cmd_candidates}
    if args.command in handlers:
        try:
            # Run the command.
            return handlers[args.command](args)
        except (InputError, OSError) as e:
            # Missing file, bad input, or a refused action.
            print(f"Input error: {e}", file=sys.stderr)
            return 2
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
