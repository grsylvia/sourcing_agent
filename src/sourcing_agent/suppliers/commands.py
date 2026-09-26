"""Supplier commands: `sourcing suppliers`, `discover`, `trial`, and `candidates`."""

# Run the async workers.
import asyncio
# Registry dates and domain ages.
import datetime
# Error output.
import sys
# File paths.
from pathlib import Path

# Async Claude client.
import anthropic

# Project files.
from ..core import paths
# Default model and usage totals.
from ..core.agent import MODEL, Usage
# Supplier list reader.
from ..core.config import load_suppliers
# Error for bad input.
from ..core.errors import InputError
# Cost model.
from ..core.pricing import estimate_cost, money
# Live and batch runners.
from ..core.runner import run_jobs
# CBOM reader.
from ..cbom.bom import read_cboms
# Sourcing-pass estimate, calibration, and logging (trials use the sourcing worker).
from ..cbom.estimate import calibrate_sourcing, log_pass, shape_range
# Conversation shapes.
from ..cbom.pipeline import pass_shape
# Candidate registry.
from .registry import approve, entry_for, load_registry, record_candidates, record_trial, reject, save_registry
# Discovery scout.
from .scout import Candidate, example_rows, gap_rows, known_domains, scout_estimate, scout_job, seed_domains
# Free screening.
from .screen import rdap_age_years, screen
# Trials.
from .trial import TRIAL_ROWS, compare_trial, run_trial, summarize_trial, trial_sample, trial_supplier
# Win rates.
from .wins import MIN_ROWS_TO_DROP, supplier_wins


def register(commands) -> None:
    """Add the supplier subcommands to the CLI."""
    # The suppliers (win rates) subcommand.
    sup = commands.add_parser("suppliers", help="Show supplier win rates per category from CBOMs and suppliers that never win.")
    sup.set_defaults(handler=cmd_suppliers)
    # Finished CBOMs to count wins in.
    sup.add_argument("cboms", type=Path, nargs="+", help="One or more CBOM CSVs.")
    # Approved supplier list.
    sup.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # The discover subcommand.
    dis = commands.add_parser("discover", help="Scout the web for new suppliers where CBOM rows went unsourced, and screen them for free.")
    dis.set_defaults(handler=cmd_discover)
    # CBOMs whose gaps pick the categories and example parts.
    dis.add_argument("cboms", type=Path, nargs="+", help="One or more CBOM CSVs.")
    # Categories to scout instead of the ones with gaps.
    dis.add_argument("--category", action="append", help="Scout this category (repeatable); default: categories with not_found/error rows.")
    # Approved supplier list.
    dis.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Opt out of the Batch API.
    dis.add_argument("--live", action="store_true", help="Run live at full price instead of through the Batch API.")
    # Plan and estimate without API calls.
    dis.add_argument("--estimate", action="store_true", help="Print the plan and cost estimate, then stop (no API calls).")
    # The trial subcommand.
    tri = commands.add_parser("trial", help="Quote sample CBOM rows on one candidate's domain only and compare with the CBOM.")
    tri.set_defaults(handler=cmd_trial)
    # Candidate to trial.
    tri.add_argument("domain", help="Candidate domain from sourcing candidates.")
    # CBOMs to sample rows from.
    tri.add_argument("--cbom", type=Path, nargs="+", required=True, help="CBOM CSVs to sample rows from.")
    # Sample size.
    tri.add_argument("--rows", type=int, default=TRIAL_ROWS, help=f"Rows to sample, unsourced first (default: {TRIAL_ROWS}).")
    # Approved supplier list (currency).
    tri.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")
    # Opt out of the Batch API.
    tri.add_argument("--live", action="store_true", help="Run live at full price instead of through the Batch API.")
    # Plan and estimate without API calls.
    tri.add_argument("--estimate", action="store_true", help="Print the plan and cost estimate, then stop (no API calls).")
    # The candidates subcommand.
    can = commands.add_parser("candidates", help="List discovered suppliers, or approve or reject one.")
    can.set_defaults(handler=cmd_candidates)
    # Approve or reject, not both.
    decide = can.add_mutually_exclusive_group()
    decide.add_argument("--approve", metavar="DOMAIN", help="Add a trialed candidate to suppliers.toml.")
    decide.add_argument("--reject", metavar="DOMAIN", help="Mark a candidate rejected so discovery skips it.")
    # Waive the trial requirement.
    can.add_argument("--without-trial", action="store_true", help="Allow --approve before a trial.")
    # Supplier list to add approved candidates to.
    can.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS, help="Supplier TOML (default: project suppliers.toml).")


def cmd_suppliers(args) -> int:
    """Print supplier win rates per category and the suppliers that never win."""
    # Win counts for every supplier and category in suppliers.toml.
    stats = supplier_wins(read_cboms(args.cboms), load_suppliers(args.suppliers)["suppliers"])
    # Table of wins.
    print(f"{'Category':<14}{'Supplier':<16}{'Wins':>5}{'Share':>7}{'Sourced rows':>14}")
    for s in stats:
        share = f"{s.wins / s.sourced:.0%}" if s.sourced else "-"
        print(f"{s.category:<14}{s.supplier:<16}{s.wins:>5}{share:>7}{s.sourced:>14}")
    # Suppliers that won nothing over enough rows.
    drops = [s for s in stats if s.drop_candidate]
    if not drops:
        print(f"\nNo drop candidates (a supplier needs 0 wins over at least {MIN_ROWS_TO_DROP} sourced rows in a category).")
        return 0
    print(f"\nDrop candidates (0 wins over at least {MIN_ROWS_TO_DROP} sourced rows):")
    for s in drops:
        print(f"- {s.supplier} from {s.category} ({s.sourced} rows)")
    print("Each drop saves about one search ($0.01) plus its result tokens per future row in that category.")
    print("Dropping changes that category's quote-cache key, so its cached quotes are re-sourced on the next run.")
    return 0


async def run_scouts(jobs: list, live: bool, usage: Usage) -> list:
    """Run scout jobs with a fresh client."""
    # One client for all scouts.
    async with anthropic.AsyncAnthropic() as client:
        return await run_jobs(client, jobs, live, usage)


def cmd_discover(args) -> int:
    """Scout categories with unsourced rows, screen the candidates for free, and save them to the registry."""
    # Approved suppliers, CBOM rows, and domains already dealt with.
    config = load_suppliers(args.suppliers)
    rows = read_cboms(args.cboms)
    registry = load_registry(paths.REGISTRY_PATH)
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
    usage = Usage()
    outcomes = asyncio.run(run_scouts([scout_job(c, example_rows(rows, c), seeds, known) for c in categories], args.live, usage))
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
    save_registry(paths.REGISTRY_PATH, registry)
    # Scorecard and cost.
    print_candidates(candidates)
    print(f"\nActual cost ({'batch' if batch else 'live'}): ${estimate_cost(usage, MODEL, batch):.2f} vs estimate {money(est)}")
    print(f"Saved to {paths.REGISTRY_PATH.name}. Next: sourcing trial <domain> --cbom <cbom.csv>")
    # Non-zero exit when a scout failed.
    return 1 if any(isinstance(o, str) for o in outcomes) else 0


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


async def run_trial_pass(sample: list[dict], supplier: dict, currency: str, live: bool, usage: Usage) -> tuple[dict, dict]:
    """Run one trial with a fresh client."""
    # One client for the trial pass.
    async with anthropic.AsyncAnthropic() as client:
        return await run_trial(client, sample, supplier, currency, live, usage)


def cmd_trial(args) -> int:
    """Quote sample CBOM rows on one candidate's domain only and compare with the CBOM."""
    # Candidate from the registry.
    registry = load_registry(paths.REGISTRY_PATH)
    domain, entry = entry_for(registry, args.domain)
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
    fit = calibrate_sourcing(paths.RUN_LOG_PATH)
    gaps = sum(r.get("status") != "sourced" for r in sample)
    # Plan.
    print(f"Trial of {entry['name']} ({domain}) on {len(sample)} rows ({gaps} unsourced, {len(sample) - gaps} sourced for a price check)")
    calibrated = f", calibrated ${fit.predict(est.mid):.2f}" if fit else ""
    print(f"Estimated cost ({'batch' if batch else 'live'}): {money(est)}{calibrated}")
    # Estimate only.
    if args.estimate:
        return 0
    # Normal sourcing pass on the candidate's domain only.
    usage = Usage()
    parts, errors = asyncio.run(run_trial_pass(sample, supplier, config["currency"], args.live, usage))
    # Row-by-row comparison with the CBOM.
    outcomes = compare_trial(sample, parts, errors)
    print(f"\n{'Part':<10}{'Outcome':<11}{'Trial':>10}{'Current':>10}  Current vendor / description")
    for o in outcomes:
        trial_price = f"${o['trial_price']:.2f}" if o["trial_price"] is not None else "-"
        current = f"${o['current_price']:.2f}" if o["current_price"] is not None else "-"
        print(f"{o['part_id']:<10}{o['outcome']:<11}{trial_price:>10}{current:>10}  {o['current_vendor'] or '-'} / {o['description']}")
    # Trial totals.
    t = summarize_trial(outcomes)
    print(f"\nQuoted {t['quoted']}/{t['rows']} rows | gaps filled {t['fills']}/{t['gaps']} | cheaper on {t['cheaper']} rows (saves ${t['savings']:.2f})")
    # Cost vs estimate, logged for calibration (same worker as sourcing).
    line = log_pass(paths.RUN_LOG_PATH, "trial", MODEL, shape, len(errors), usage, batch, f"trial {domain}", fit)
    if line:
        print(line)
    # Record the trial.
    record_trial(entry, t, outcomes, datetime.date.today())
    save_registry(paths.REGISTRY_PATH, registry)
    print(f"Saved to {paths.REGISTRY_PATH.name}. Approve with: sourcing candidates --approve {domain}")
    return 0


def cmd_candidates(args) -> int:
    """List discovered suppliers, or approve (add to suppliers.toml) or reject one."""
    # Registry of discovered domains.
    registry = load_registry(paths.REGISTRY_PATH)
    today = datetime.date.today()
    # Approve: append a commented [[suppliers]] entry.
    if args.approve:
        domain, entry = approve(registry, args.approve, args.suppliers, args.without_trial, today)
        save_registry(paths.REGISTRY_PATH, registry)
        print(f"Added {entry['name']} ({domain}) to {args.suppliers.name} for {', '.join(entry['categories'])}.")
        print("Note: cached quotes for those categories are re-sourced on the next run (the supplier list is part of the cache key).")
        return 0
    # Reject: discovery will skip the domain from now on.
    if args.reject:
        domain = reject(registry, args.reject, today)
        save_registry(paths.REGISTRY_PATH, registry)
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
