"""Runs worker jobs live (a few at a time, full price) or through the Message Batches API (50% off every token)."""

# Concurrency and poll delays.
import asyncio
# Progress lines.
import logging
# Any worker's parsed result.
from typing import Any

# Async Claude client and API errors.
import anthropic

# Agent loop shared with every worker.
from .agent import Job, Usage, handle_response, request_params, run_live
# Worker failure type.
from .errors import WorkerError

# Worker conversations running at once in live mode.
MAX_PARALLEL = 4
# Seconds between batch status checks.
POLL_SECONDS = 30

# Logger for progress lines.
log = logging.getLogger(__name__)


async def run_jobs(client: anthropic.AsyncAnthropic, jobs: list[Job], live: bool, usage: Usage) -> list[Any | str]:
    """Run every job to completion; return its parsed submission or an error message, in job order."""
    # Nothing to run.
    if not jobs:
        return []
    # Pick the runner for the mode.
    return await (run_live_jobs(client, jobs, usage) if live else run_batch_jobs(client, jobs, usage))


async def run_live_jobs(client: anthropic.AsyncAnthropic, jobs: list[Job], usage: Usage) -> list[Any | str]:
    """Live mode: stream each job's conversation, MAX_PARALLEL at a time."""
    # Limit concurrent conversations.
    gate = asyncio.Semaphore(MAX_PARALLEL)

    async def one(job: Job) -> Any | str:
        """Run one job; return its result or an error message."""
        # Wait for a free slot.
        async with gate:
            try:
                # Run the conversation.
                return await run_live(client, job, usage)
            except anthropic.AuthenticationError:
                # Bad credentials stop the whole run.
                raise
            except (WorkerError, anthropic.APIError) as e:
                # Other failures mark only this job as errored.
                return str(e)

    # Run all jobs.
    return await asyncio.gather(*(one(job) for job in jobs))


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


async def run_batch_jobs(client: anthropic.AsyncAnthropic, jobs: list[Job], usage: Usage) -> list[Any | str]:
    """Batch mode: run every job in batch rounds, one request per unfinished job per round."""
    # Result per job index.
    results: dict[int, Any | str] = {}
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
                results[i] = f"{jobs[i].label}: batch request {item.result.type}"
                pending.pop(i, None)
                continue
            try:
                # Result, or None when another turn is needed.
                result = handle_response(jobs[i], item.result.message, usage)
            except WorkerError as e:
                # Refusal, truncation, or out of turns.
                results[i] = str(e)
                pending.pop(i, None)
                continue
            # Finished jobs leave the pending set.
            if result is not None:
                results[i] = result
                pending.pop(i, None)
    # Results in job order.
    return [results[i] for i in range(len(jobs))]
