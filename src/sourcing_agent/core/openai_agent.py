"""OpenAI Responses adapter for domain-filtered sourcing and strict submissions."""

# Decode function-call arguments.
import json
# Sum normalized usage counters.
from dataclasses import fields

# Shared worker state and limits.
from .agent import EFFORT, MAX_TOKENS, MAX_TURNS, Job, Usage
# Malformed, refused, or incomplete responses.
from .errors import WorkerError


def request_params(job: Job) -> dict:
    """Translate the shared job's tools and opening messages into Responses parameters."""
    # Reuse the worker's domain lock and search budget.
    search = next(t for t in job.tools if t.get("name") == "web_search")
    # An empty supplier filter must never become an unrestricted search.
    if "allowed_domains" in search and not search["allowed_domains"]:
        raise WorkerError(f"{job.label}: no approved domains for OpenAI web search")
    # Reject oversized domain lists instead of silently weakening the lock.
    if len(search.get("allowed_domains", [])) > 100:
        raise WorkerError(f"{job.label}: OpenAI supports at most 100 allowed domains")
    # OpenAI combines search and page opening in its hosted web tool.
    web = {"type": "web_search", "user_location": {"type": "approximate", "country": "US"}}
    # Restricted workers must carry the full approved-domain list.
    if "allowed_domains" in search:
        web["filters"] = {"allowed_domains": search["allowed_domains"]}
    # Convert the final strict submission tool.
    submit = next(t for t in job.tools if t.get("name") == job.submit_tool)
    # Translate only the opening messages; later turns already use Responses items.
    messages = []
    # Preserve the shared prefix separately from each batch's rows.
    for message in job.messages:
        # Plain string messages already have the Responses shape.
        if isinstance(message.get("content"), list) and message.get("role") == "user":
            # Map Anthropic text blocks without its cache-control fields.
            content = [{"type": "input_text", "text": b["text"]} for b in message["content"]]
            # Preserve explicit shared-prefix caching on GPT-6.
            for old, new in zip(message["content"], content):
                # The original breakpoint identifies the stable category instructions.
                if "cache_control" in old:
                    new["prompt_cache_breakpoint"] = {"mode": "explicit"}
            # Add the converted opening message.
            messages.append({"role": message["role"], "content": content})
        else:
            # Output items and nudges can be replayed unchanged.
            messages.append(message)
    # The Responses API parameters omit Anthropic-only settings.
    return {
        "model": job.model,
        "instructions": job.system,
        "input": messages,
        "tools": [web, {"type": "function", "name": submit["name"], "description": submit["description"], "strict": True, "parameters": submit["input_schema"]}],
        "reasoning": {"effort": EFFORT},
        "max_output_tokens": MAX_TOKENS,
        "max_tool_calls": search.get("max_uses", 12),
        "parallel_tool_calls": False,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }


def handle_response(job: Job, response, usage: Usage):
    """Normalize metering, validate the submission, or prepare a bounded continuation."""
    # Count even failed or incomplete billable responses.
    job.turns += 1
    # Usage may be absent on a failed response.
    u = response.usage
    # Cached and written input are subsets of OpenAI's total input tokens.
    details = getattr(u, "input_tokens_details", None)
    # Normalize to the same disjoint meters used by Anthropic.
    read = getattr(details, "cached_tokens", 0) or 0
    # Newer model cache-write meters are preserved by SDK extra fields.
    write = getattr(details, "cache_write_tokens", 0) or 0
    # Hosted search calls include search, page opening, and in-page find actions.
    calls = [item for item in response.output if item.type == "web_search_call"]
    # Bill search actions and track page opening separately for learning.
    delta = Usage(
        requests=1,
        input_tokens=max(0, (getattr(u, "input_tokens", 0) or 0) - read - write),
        cache_read_tokens=read,
        cache_write_tokens=write,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        web_searches=sum(getattr(item.action, "type", "") == "search" for item in calls),
        web_fetches=sum(getattr(item.action, "type", "") == "open_page" for item in calls),
    )
    # Add usage to both the pass and this conversation.
    for target in (usage, job.usage):
        # Keep every shared usage meter in sync.
        for meter in fields(Usage):
            # Accumulate this response once.
            setattr(target, meter.name, getattr(target, meter.name) + getattr(delta, meter.name))
    # Do not trust truncated or failed function arguments.
    if response.status != "completed":
        raise WorkerError(f"{job.label}: OpenAI response {response.status} ({response.id})")
    # Refusals end the job without nudging around them.
    if any(getattr(b, "type", "") == "refusal" for item in response.output for b in getattr(item, "content", [])):
        raise WorkerError(f"{job.label}: OpenAI request refused ({response.id})")
    # Require a single call to the known submission function.
    submissions = [item for item in response.output if item.type == "function_call"]
    # Unknown or multiple calls cannot safely be interpreted as one result.
    if submissions:
        # Validate the function identity.
        if len(submissions) != 1 or submissions[0].name != job.submit_tool:
            raise WorkerError(f"{job.label}: unexpected OpenAI function calls")
        # Decode and validate using the existing quote parser.
        try:
            # Every provider uses the same supplier and price validation.
            return job.parse(json.loads(submissions[0].arguments))
        except (ValueError, TypeError, KeyError) as error:
            # Malformed output becomes a row error rather than aborting the run.
            raise WorkerError(f"{job.label}: invalid OpenAI submission: {error}") from error
    # Bound continuations that never submit.
    if job.turns >= MAX_TURNS:
        raise WorkerError(f"{job.label}: no submission after {MAX_TURNS} turns")
    # Retain reasoning and web-search output items for the next request.
    job.messages.extend(item.model_dump(exclude_none=True) for item in response.output)
    # Ask only for the already-researched submission.
    job.messages.append({"role": "user", "content": f"Call {job.submit_tool} now with everything you found."})
    # A further turn is required.
    return None


async def run_live(client, job: Job, usage: Usage):
    """Run a bounded Responses conversation and return its validated quotes."""
    # Continue until submission or a handled failure.
    while True:
        # Send one request using the provider's standard service tier.
        response = await client.responses.create(**request_params(job), service_tier="default")
        # Parse or prepare the next turn.
        result = handle_response(job, response, usage)
        # Return the validated result once complete.
        if result is not None:
            return result
