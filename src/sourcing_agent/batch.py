"""Batch mode: runs worker jobs through the Message Batches API (50% off every token)."""

# Poll delay between status checks.
import asyncio
# Progress lines.
import logging

# Async Claude client.
import anthropic

# Worker job helpers shared with live mode.
from .worker import Job, PartQuotes, Usage, WorkerError, handle_response, request_params

# Seconds between batch status checks.
POLL_SECONDS = 30

# Logger for progress lines.
log = logging.getLogger(__name__)


async def _wait(client: anthropic.AsyncAnthropic, batch_id: str) -> None:
    """Poll a batch until it has ended."""
    # Check status until processing ends.
    while True:
        # Current batch state.
        batch = await client.messages.batches.retrieve(batch_id)
        # Per-request counts.
        c = batch.request_counts
        # Report progress.
        log.info("batch %s: %s (processing %d, succeeded %d, errored %d)", batch_id, batch.processing_status, c.processing, c.succeeded, c.errored)
        # Stop once every request has finished.
        if batch.processing_status == "ended":
            return
        # Wait before checking again.
        await asyncio.sleep(POLL_SECONDS)


async def run_jobs(client: anthropic.AsyncAnthropic, jobs: list[Job], usage: Usage) -> list[list[PartQuotes] | str]:
    """Run every job to completion in batch rounds; return quotes or an error message per job."""
    # Result per job index.
    results: dict[int, list[PartQuotes] | str] = {}
    # Jobs still needing a turn.
    pending = dict(enumerate(jobs))
    # Round counter for log lines and request IDs.
    round_no = 0
    # One batch per round until every job has a result.
    while pending:
        # Next round.
        round_no += 1
        # One request per pending job.
        requests = [{"custom_id": f"job{i}-r{round_no}", "params": request_params(job)} for i, job in pending.items()]
        # Submit the round.
        batch = await client.messages.batches.create(requests=requests)
        # Announce the round.
        log.info("batch %s: round %d submitted with %d requests", batch.id, round_no, len(requests))
        # Wait for it to finish.
        await _wait(client, batch.id)
        # Read each result.
        async for item in await client.messages.batches.results(batch.id):
            # Job this result belongs to.
            i = int(item.custom_id.split("-")[0].removeprefix("job"))
            # Failed, canceled, or expired requests end their job.
            if item.result.type != "succeeded":
                results[i] = f"{jobs[i].category}: batch request {item.result.type}"
                pending.pop(i, None)
                continue
            try:
                # Quotes, or None when another turn is needed.
                parts = handle_response(jobs[i], item.result.message, usage)
            except WorkerError as e:
                # Refusal, truncation, or out of turns.
                results[i] = str(e)
                pending.pop(i, None)
                continue
            # Finished jobs leave the pending set.
            if parts is not None:
                results[i] = parts
                pending.pop(i, None)
    # Results in job order.
    return [results[i] for i in range(len(jobs))]
