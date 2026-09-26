"""Agent loop shared by every worker: one Messages API conversation that ends when the worker calls its submit tool."""

# Typed conversation state and usage totals.
from dataclasses import dataclass
# Per-request usage lines.
import logging
# Per-job submission parsers.
from typing import Any, Callable

# Async Claude client used in live mode.
import anthropic

# Worker failure type.
from .errors import WorkerError

# Default model for every worker.
MODEL = "claude-sonnet-5"
# Stronger model that retries rows the default model could not source.
ESCALATION_MODEL = "claude-opus-5-5"
# Thinking depth.
EFFORT = "medium"
# Output cap per request, with room for thinking.
MAX_TOKENS = 64000
# Cap on request round-trips (pause_turn resumes plus nudges).
MAX_TURNS = 6

# Logger for per-request usage.
log = logging.getLogger(__name__)


# Token and tool usage summed over requests.
@dataclass
class Usage:
    # Messages API requests made.
    requests: int = 0
    # Uncached input tokens.
    input_tokens: int = 0
    # Input tokens written to the prompt cache.
    cache_write_tokens: int = 0
    # Input tokens read from the prompt cache.
    cache_read_tokens: int = 0
    # Output tokens, including thinking.
    output_tokens: int = 0
    # Web searches run (billed per search).
    web_searches: int = 0
    # Web pages fetched.
    web_fetches: int = 0

    def add(self, u) -> None:
        """Add one response's usage to the totals."""
        # Count the request.
        self.requests += 1
        # Uncached input.
        self.input_tokens += u.input_tokens or 0
        # Cache writes.
        self.cache_write_tokens += u.cache_creation_input_tokens or 0
        # Cache reads.
        self.cache_read_tokens += u.cache_read_input_tokens or 0
        # Output tokens.
        self.output_tokens += u.output_tokens or 0
        # Server tool counts, when present.
        if u.server_tool_use:
            # Searches run.
            self.web_searches += u.server_tool_use.web_search_requests or 0
            # Pages fetched.
            self.web_fetches += u.server_tool_use.web_fetch_requests or 0


# One worker conversation, shared by live and batch modes.
@dataclass
class Job:
    # What the job covers, for log lines and errors (e.g. "bearings [B-1, B-2]").
    label: str
    # Standing instructions for this kind of worker.
    system: str
    # Tool definitions sent on every request.
    tools: list[dict]
    # Conversation so far.
    messages: list[dict]
    # Client tool the worker calls with its final result.
    submit_tool: str
    # Turns the submission's input into the job's result.
    parse: Callable[[dict], Any]
    # Model that runs this conversation.
    model: str = MODEL
    # Requests answered so far.
    turns: int = 0


def request_params(job: Job) -> dict:
    """Return the Messages API parameters for the job's next request."""
    # Same shape for live and batch requests.
    return {
        "model": job.model,
        "max_tokens": MAX_TOKENS,
        "system": job.system,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": EFFORT},
        "tools": job.tools,
        "messages": job.messages,
        "cache_control": {"type": "ephemeral"},
    }


def handle_response(job: Job, response, usage: Usage) -> Any | None:
    """Process one response: return the parsed submission, or None when another turn is needed."""
    # Count this turn.
    job.turns += 1
    # Add this request to the usage totals.
    usage.add(response.usage)
    # Shorthand for this response's usage.
    u = response.usage
    # Log this request's usage.
    log.info(
        "%s %s: %s | in %d, cache write %d, cache read %d, out %d",
        job.model, job.label, response.stop_reason,
        u.input_tokens or 0, u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0, u.output_tokens or 0,
    )
    # A refusal ends the job.
    if response.stop_reason == "refusal":
        raise WorkerError(f"{job.label}: request refused ({response.id})")
    # Truncated output cannot be trusted.
    if response.stop_reason == "max_tokens":
        raise WorkerError(f"{job.label}: hit max_tokens ({response.id})")
    # Look for the final submission.
    submission = next((b for b in response.content if b.type == "tool_use" and b.name == job.submit_tool), None)
    # Validate and return the submission with the job's parser.
    if submission is not None:
        return job.parse(submission.input)
    # Out of turns without a submission.
    if job.turns >= MAX_TURNS:
        raise WorkerError(f"{job.label}: no submission after {MAX_TURNS} turns")
    # Keep the assistant turn exactly as returned so the next request continues it.
    job.messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})
    # Finished without submitting (not paused): ask for the submission.
    if response.stop_reason != "pause_turn":
        job.messages.append({"role": "user", "content": f"Call {job.submit_tool} now with everything you found."})
    # Another turn is needed.
    return None


async def run_live(client: anthropic.AsyncAnthropic, job: Job, usage: Usage) -> Any:
    """Live mode: run one conversation to completion and return its parsed submission."""
    # Loop until the worker submits (handle_response raises when out of turns).
    while True:
        # One streamed Messages API request.
        async with client.messages.stream(**request_params(job)) as stream:
            # Wait for the complete response.
            response = await stream.get_final_message()
        # Result, or None when another turn is needed.
        result = handle_response(job, response, usage)
        # Done once the worker submits.
        if result is not None:
            return result
