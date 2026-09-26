"""Execute one sourcing pass; trials and full BOM runs use the same boundary."""

# Per-pass learning facts.
from dataclasses import dataclass
# Progress messages.
import logging
# Either supported provider client.
from typing import Any
# API usage accumulated by workers.
from ..core.agent import Usage
# Category-specific approved suppliers.
from ..core.config import category_suppliers
# Setup failures count as errors, not missing quotes.
from ..core.errors import WorkerError
# Provider execution boundary.
from ..core.runner import run_jobs
# Conversation-level learning records.
from ..learning.runlog import conversation_record
# Deterministic batching.
from .planning import make_batches
# Validated quotes and the price rule.
from .quotes import PartQuotes, pick_lowest
# Sourcing worker construction.
from .worker import new_job

# Progress messages from each sourcing pass.
log = logging.getLogger(__name__)


# Facts emitted as soon as a complete pass is available.
@dataclass
class SourcingPass:
    # First or escalation pass.
    name: str
    # Model actually used.
    model: str
    # Workload of each conversation.
    shape: list[tuple[int, int]]
    # Number of failed rows before a later retry.
    error_rows: int
    # Actual metered requests and tokens.
    usage: Usage
    # Supplier evidence and conversation outcomes.
    conversations: list[dict]


async def source_rows(
    client: Any,
    rows: list[dict],
    config: dict,
    live: bool,
    model: str,
    usage: Usage,
) -> tuple[dict[str, PartQuotes], dict[str, str], list[dict]]:
    """Source rows on one model with the config's suppliers; return quotes and errors by part ID, and one learning record per conversation."""
    # Worker-sized batches by category.
    batches = make_batches(rows)
    # Outcome per batch: quotes, or an error message.
    outcomes: list[list[PartQuotes] | str] = [""] * len(batches)
    # Jobs that could be set up, with their batch index.
    jobs = []
    for i, (category, batch) in enumerate(batches):
        try:
            # Tools and first message for this batch.
            jobs.append((i, new_job(category, batch, config["suppliers"], config["currency"], model)))
        except WorkerError as e:
            # No suppliers for the category.
            outcomes[i] = str(e)
    # Run the jobs and put each result in its batch slot.
    for (i, _), result in zip(jobs, await run_jobs(client, [job for _, job in jobs], live, usage)):
        outcomes[i] = result
    # One learning record per conversation that ran, with rows quoted as its outcome.
    conversations = [
        conversation_record(job, "error", 0) if isinstance(outcomes[i], str)
        else conversation_record(job, "submitted", sum(bool(p.quotes) for p in outcomes[i]))
        for i, job in jobs
    ]
    # Attach per-row supplier evidence to the conversation learning records.
    for (i, job), conversation in zip(jobs, conversations):
        # Approved suppliers and requested rows are recorded even for worker errors.
        category, batch = batches[i]
        # Normalize worker failures separately from unsuccessful searches.
        conversation["supplier_outcomes"] = supplier_evidence(batch, outcomes[i], category_suppliers(category, config["suppliers"]))
    # Quotes and errors by part ID.
    parts: dict[str, PartQuotes] = {}
    errors: dict[str, str] = {}
    for (category, batch), outcome in zip(batches, outcomes):
        # A string outcome is an error for every row in the batch.
        if isinstance(outcome, str):
            log.warning("%s %s [%s]: error: %s", model, category, ", ".join(r["part_id"] for r in batch), outcome)
            errors.update({r["part_id"]: outcome for r in batch})
            continue
        # Quotes for each row in the batch.
        parts.update({part.part_id: part for part in outcome})
    # Results for every row, plus the conversation records.
    return parts, errors, conversations


def supplier_evidence(rows: list[dict], outcome: list[PartQuotes] | str, suppliers: list[dict]) -> list[dict]:
    """Record attempts, valid quotes, and wins without mistaking missing evidence for failure."""
    # Results indexed by requested row.
    parts = {} if isinstance(outcome, str) else {p.part_id: p for p in outcome}
    # One learning observation per row and approved supplier.
    observations = []
    # Keep every requested row, including omitted submissions.
    for row in rows:
        # Missing parts have no proven searches.
        part = parts.get(row["part_id"], PartQuotes(row["part_id"]))
        # Price-rule winner at this run's BOM quantity.
        winner = pick_lowest(part, int(row["quantity"]))
        # Explicit search evidence from the parser.
        reports = {o["supplier"]: o for o in part.supplier_outcomes}
        # Record the actual supplier identity and domains used in this pass.
        for supplier in suppliers:
            # A worker error is distinct from a supplier search finding nothing.
            report = reports.get(supplier["name"], {"status": "error" if isinstance(outcome, str) else "not_checked", "reason": outcome if isinstance(outcome, str) else "No search outcome reported"})
            # Every validated quote counts, including quotes that lose on price.
            quoted = any(q.vendor == supplier["name"] for q in part.quotes)
            # Preserve evidence and the winner for later multi-run learning.
            observations.append({"part_id": row["part_id"], "supplier": supplier["name"], "domains": sorted(supplier["domains"]), "status": "quoted" if quoted else report["status"], "reason": report["reason"], "won": bool(winner and winner.quote.vendor == supplier["name"])})
    # Structured evidence is stored inside the normal run log.
    return observations
