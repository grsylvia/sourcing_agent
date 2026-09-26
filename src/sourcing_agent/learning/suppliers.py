"""Supplier reliability learned from explicit search outcomes across sourcing runs."""

# Qualifying runs must each contain enough checked rows.
MIN_ROWS_TO_DROP = 5
# One poor run cannot justify removing a supplier.
MIN_RUNS_TO_DROP = 3


def supplier_history(records: list[dict]) -> dict[tuple, dict]:
    """Aggregate evidence by category, supplier, and domains; old records prove no failures."""
    # State for each supplier identity and category.
    history = {}
    # Repeated log entries must not inflate the failure streak.
    seen = set()
    # Log order is execution order.
    for record in records:
        # Only identified passes with explicit evidence can inform pruning.
        run_id = record.get("run_id")
        # Older logs remain usable elsewhere without fabricating supplier outcomes.
        if not run_id or run_id in seen:
            # Skip missing or repeated identities.
            continue
        # Mark this pass as seen.
        seen.add(run_id)
        # Merge worker batches into one set of observations per supplier per pass.
        groups = {}
        # Each conversation owns one category.
        for conversation in record.get("conversations") or []:
            # Outcomes contain actual searched supplier identities.
            for outcome in conversation.get("supplier_outcomes", []):
                # Domain changes start a fresh supplier history.
                key = (conversation["category"], outcome["supplier"], tuple(sorted(outcome["domains"])))
                # Deduplicate row IDs within a pass conservatively.
                rows = groups.setdefault(key, {})
                # Conflicting duplicate observations cannot prove failure.
                previous = rows.get(outcome["part_id"])
                # Default to the current observation.
                merged = outcome
                # Preserve success or mark repeated failure evidence as unknown.
                if previous is not None and outcome["status"] != "quoted":
                    # A repeated row must never strengthen evidence for removal.
                    merged = previous if previous["status"] == "quoted" else outcome | {"status": "not_checked"}
                # Store one observation for each row.
                rows[outcome["part_id"]] = merged
        # Update each supplier only once per pass.
        for key, rows in groups.items():
            # Counts are explicit outcomes, independent of winning on price.
            counts = {status: sum(o["status"] == status for o in rows.values()) for status in ("quoted", "no_quote", "error", "not_checked")}
            # Initialize a supplier's evidence summary.
            stats = history.setdefault(key, {"quoted": 0, "no_quote": 0, "error": 0, "not_checked": 0, "wins": 0, "failed_runs": 0})
            # Preserve all pass outcomes, including trials and retries, for learning.
            for status, count in counts.items():
                # Accumulate this meter.
                stats[status] += count
            # Wins are informational and never define failure.
            stats["wins"] += sum(bool(o.get("won")) for o in rows.values())
            # Any valid quote breaks the streak, including an escalation or trial rescue.
            if counts["quoted"]:
                # Success overrides previous failures.
                stats["failed_runs"] = 0
            # Only ordinary first passes can add independent failing runs.
            elif record.get("pass") == "first":
                # Errors, incomplete checks, and undersized samples cannot sustain a streak.
                qualifies = counts["no_quote"] >= MIN_ROWS_TO_DROP and not counts["error"] and not counts["not_checked"]
                # Require consecutive adequately checked runs.
                stats["failed_runs"] = stats["failed_runs"] + 1 if qualifies else 0
            # Recommendation only; editing the supplier list still requires user selection.
            stats["drop_candidate"] = stats["failed_runs"] >= MIN_RUNS_TO_DROP
    # Shared by the supplier command and learning report.
    return history
