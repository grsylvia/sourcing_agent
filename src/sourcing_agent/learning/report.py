"""Learning report: where tokens and dollars go per pass, settings, category, and conversation, and what to change next."""

# Usage records.
from ..core.agent import Usage
# Cost model.
from ..core.pricing import cost_by_meter, estimate_cost
# Median helper.
from .profile import percentile

# Meter share of spend that triggers a recommendation.
SHARE_THRESHOLDS = {"cache_read": 0.35, "output": 0.25, "input": 0.20, "searches": 0.20}
# Category coverage below this (with enough rows) suggests supplier discovery.
LOW_COVERAGE = 0.8
# Rows a category needs before its coverage counts.
MIN_CATEGORY_ROWS = 3
# A conversation costing this many times the median is an outlier.
OUTLIER_FACTOR = 3


def mode(record: dict) -> str:
    """'batch' or 'live' for a pass record."""
    # Mode label.
    return "batch" if record["batch"] else "live"


def group_key(record: dict) -> tuple[str, str, str]:
    """(model, mode, settings ID) a pass belongs to; older records group under 'untagged'."""
    # Grouping key.
    return record["model"], mode(record), record.get("settings_id") or "untagged"


def efficiency(records: list[dict]) -> list[dict]:
    """Cost per row and per quoted row, and coverage, for each (model, mode, settings) group."""
    # Records per group.
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault(group_key(r), []).append(r)
    # One summary per group.
    rows = []
    for (model, run_mode, sid), recs in groups.items():
        # Totals.
        cost = sum(r["actual_cost"] for r in recs)
        n_rows = sum(r["rows"] for r in recs)
        # Quoted rows are known only for records that logged conversations.
        known = [r for r in recs if r.get("quoted_rows") is not None]
        quoted = sum(r["quoted_rows"] for r in known) if known else None
        known_rows = sum(r["rows"] for r in known)
        rows.append({
            "model": model, "mode": run_mode, "settings_id": sid, "passes": len(recs), "rows": n_rows, "cost": cost,
            "cost_per_row": cost / n_rows if n_rows else None,
            "quoted": quoted,
            "coverage": quoted / known_rows if quoted is not None and known_rows else None,
            "cost_per_quoted": sum(r["actual_cost"] for r in known) / quoted if quoted else None,
        })
    # Cheapest per row first.
    return sorted(rows, key=lambda g: (g["cost_per_row"] is None, g["cost_per_row"] or 0))


def meter_totals(records: list[dict]) -> dict[str, float]:
    """Dollars per meter across all passes."""
    # Running totals.
    totals: dict[str, float] = {}
    for r in records:
        for meter, dollars in (cost_by_meter(Usage(**r["usage"]), r["model"], r["batch"]) or {}).items():
            totals[meter] = totals.get(meter, 0.0) + dollars
    # Totals per meter.
    return totals


def conversation_costs(records: list[dict]) -> list[dict]:
    """Every logged conversation with its dollar cost (priced at its pass's model and mode)."""
    # Flatten with cost.
    return [c | {"model": r["model"], "cost": estimate_cost(Usage(**c["usage"]), r["model"], r["batch"])} for r in records for c in r.get("conversations") or []]


def by_category(convs: list[dict]) -> list[dict]:
    """Rows, coverage, cost per row, turns, and context re-reads per category."""
    # Conversations per category.
    groups: dict[str, list[dict]] = {}
    for c in convs:
        groups.setdefault(c.get("category", "?"), []).append(c)
    # One summary per category.
    out = []
    for category, cs in groups.items():
        rows = sum(c["rows"] for c in cs)
        quoted = sum(c["quoted_rows"] for c in cs)
        cost = sum(c["cost"] for c in cs)
        # Re-reads of gathered context per conversation.
        rereads = [c["usage"]["cache_read_tokens"] / max(1, c["usage"]["input_tokens"] + c["usage"]["cache_write_tokens"]) for c in cs]
        out.append({
            "category": category, "conversations": len(cs), "rows": rows, "quoted": quoted,
            "coverage": quoted / rows if rows else 0.0, "cost": cost, "cost_per_row": cost / rows if rows else 0.0,
            "cost_per_quoted": cost / quoted if quoted else None,
            "turns": sum(c["turns"] for c in cs) / len(cs), "rereads": percentile(rereads, 50),
        })
    # Most expensive per row first.
    return sorted(out, key=lambda g: -g["cost_per_row"])


def outliers(convs: list[dict], top: int = 3) -> list[dict]:
    """The costliest conversations, flagged when far above the median."""
    # Nothing logged.
    if not convs:
        return []
    # Median conversation cost.
    median = percentile([c["cost"] for c in convs], 50)
    # Costliest first, flagged against the median.
    return [c | {"times_median": c["cost"] / median if median else None} for c in sorted(convs, key=lambda c: -c["cost"])[:top]]


def batch_savings(records: list[dict]) -> float:
    """What live passes would have saved in batch mode (tokens at half price; search fees unchanged)."""
    # Live cost minus the same usage priced as batch.
    return sum(r["actual_cost"] - estimate_cost(Usage(**r["usage"]), r["model"], True) for r in records if not r["batch"])


def recommendations(records: list[dict], groups: list[dict], meters: dict[str, float], categories: list[dict], worst: list[dict]) -> list[str]:
    """Plain-language next steps for fewer tokens and lower cost, from the logged data."""
    # Nothing to learn from yet.
    if not records:
        return ["No passes logged yet: run sourcing once to start learning."]
    tips = []
    # Share of spend per meter.
    total = sum(meters.values()) or 1.0
    share = {m: d / total for m, d in meters.items()}
    # Median context re-reads, when conversations were logged.
    rereads = [c["rereads"] for c in categories]
    reread_text = f" (median ≈{percentile(rereads, 50):.0f}× per conversation)" if rereads else ""
    if share.get("cache_read", 0) >= SHARE_THRESHOLDS["cache_read"]:
        tips.append(f"Context re-reads are {share['cache_read']:.0%} of spend{reread_text}: cut steps per conversation (effort low, review consistently failing suppliers in heavy categories, or a fetch cap).")
    if share.get("output", 0) >= SHARE_THRESHOLDS["output"]:
        tips.append(f"Output and thinking are {share['output']:.0%} of spend: trial effort low on a few rows.")
    if share.get("input", 0) >= SHARE_THRESHOLDS["input"]:
        tips.append(f"Uncached input is {share['input']:.0%} of spend: check the shared cache prefix is being read.")
    if share.get("searches", 0) >= SHARE_THRESHOLDS["searches"]:
        tips.append(f"Search fees are {share['searches']:.0%} of spend: review repeated search failures (sourcing suppliers <cbom>); zero wins alone never justifies a drop.")
    # Batch mode.
    saved = batch_savings(records)
    if saved > 0.005:
        tips.append(f"Live passes cost ${saved:.2f} more than batch would have: use batch when results can wait.")
    # Coverage gaps.
    gaps = [c["category"] for c in categories if c["rows"] >= MIN_CATEGORY_ROWS and c["coverage"] < LOW_COVERAGE]
    if gaps:
        tips.append(f"Low coverage in {', '.join(gaps)}: every unquoted row still costs tokens; run sourcing discover <cbom> for those categories.")
    # Escalation value.
    first = [r for r in records if r["pass"] == "first" and r.get("quoted_rows")]
    retry = [r for r in records if r["pass"] == "escalation" and r.get("quoted_rows") is not None]
    if first and retry:
        f_cost = sum(r["actual_cost"] for r in first) / sum(r["quoted_rows"] for r in first)
        r_quoted = sum(r["quoted_rows"] for r in retry)
        r_rows = sum(r["rows"] for r in retry)
        r_cost = sum(r["actual_cost"] for r in retry)
        per = f"${r_cost / r_quoted:.2f}/quoted row" if r_quoted else "no quoted rows"
        tips.append(f"Opus retries quoted {r_quoted}/{r_rows} rows ({per} vs ${f_cost:.2f} on the first pass): keep them only if that rescue rate is worth it (--no-escalate).")
    # Settings comparison within the same model and mode.
    comparable: dict[tuple, list[dict]] = {}
    for g in groups:
        if g["cost_per_quoted"] is not None:
            comparable.setdefault((g["model"], g["mode"]), []).append(g)
    for (model, run_mode), gs in comparable.items():
        if len(gs) > 1:
            best, worst_g = min(gs, key=lambda g: g["cost_per_quoted"]), max(gs, key=lambda g: g["cost_per_quoted"])
            tips.append(f"{model} {run_mode}: settings {best['settings_id']} is cheapest per quoted row (${best['cost_per_quoted']:.2f} vs ${worst_g['cost_per_quoted']:.2f} for {worst_g['settings_id']}).")
    # Outlier conversations.
    for c in worst:
        if c["times_median"] and c["times_median"] >= OUTLIER_FACTOR:
            tips.append(f"Outlier: {c['label']} cost ${c['cost']:.2f} ({c['times_median']:.0f}× median, {c['turns']} turns, {c['usage']['cache_read_tokens']:,} cache reads): a turn or fetch cap would bound it.")
    # Default when nothing stands out.
    return tips or ["No clear waste yet: keep logging runs."]
