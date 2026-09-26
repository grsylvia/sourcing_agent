"""Offline contract tests for provider routing, costs, and quote preservation."""

# Run asynchronous provider loops without network access.
import asyncio
# Capture CLI reports.
import contextlib
# Buffer printed summaries.
import io
# Build and inspect API JSON.
import json
# Isolate cache and log files.
import tempfile
# Standard-library test runner.
import unittest
# Fixture paths.
from pathlib import Path
# Lightweight parsed API responses.
from types import SimpleNamespace
# Stub API boundaries and credentials.
from unittest.mock import AsyncMock, patch

# Real SDK request serialization over a local mock transport.
import httpx2
# OpenAI client used only with the mock transport.
import openai
# Typed response deserialization.
from openai.types.responses import Response

# CLI entry point.
from sourcing_agent.cli import main
# Worker and provider contracts.
from sourcing_agent.core.agent import MAX_TURNS, Usage
# OpenAI request and response adapters.
from sourcing_agent.core.openai_agent import handle_response, request_params
# Shared job runner.
from sourcing_agent.core.runner import run_jobs
# Input and worker errors.
from sourcing_agent.core.errors import InputError, WorkerError
# Cost accounting.
from sourcing_agent.core.pricing import cost_by_meter, estimate_cost
# Provider selection.
from sourcing_agent.core.providers import create_client, select_models
# Sourcing pipeline and model attribution.
from sourcing_agent.cbom.pipeline import source_bom
# Build real sourcing jobs and quote submissions.
from sourcing_agent.cbom.worker import new_job
# Learned estimates and provider isolation.
from sourcing_agent.cbom.estimate import learned, sourcing_estimator
# Run summary output and logging.
from sourcing_agent.cbom.commands import print_run
# Pass logging with normalized usage.
from sourcing_agent.learning.runlog import record_pass
# Cost range for synthetic log records.
from sourcing_agent.core.pricing import Range

# One row with one approved supplier.
ROWS = [{"part_id": "B1", "category": "bearings", "description": "608 bearing", "quantity": "2", "spec": "8x22x7 mm", "mfr_part_number": ""}]
# Supplier identity and allowed domain.
SUPPLIERS = [{"name": "Example", "domains": ["example.com"], "categories": ["bearings"]}]
# Complete valid quote returned by either provider.
SUBMISSION = {"parts": [{"part_id": "B1", "notes": "", "quotes": [{"vendor": "Example", "vendor_part_number": "608", "url": "https://example.com/608", "pack_size": 1, "min_order_packs": 1, "price_breaks": [{"min_packs": 1, "pack_price": 2.0}], "match_notes": "matches"}]}]}


def job(model="gpt-6-sol"):
    """Use production job construction in adapter tests."""
    # Keep the domain lock and parser identical to real sourcing.
    return new_job("bearings", ROWS, SUPPLIERS, "USD", model)


def payload(submit=True, status="completed", arguments=None):
    """Create a Responses API fixture with distinct input billing meters."""
    # Include search and page-open actions to distinguish fees from retrieval.
    output = [{"id": "ws1", "type": "web_search_call", "status": "completed", "action": {"type": "search", "query": "608"}}, {"id": "ws2", "type": "web_search_call", "status": "completed", "action": {"type": "open_page", "url": "https://example.com/608"}}]
    # A completed function call carries the same strict quote schema as Claude.
    if submit:
        output.append({"id": "fc1", "type": "function_call", "call_id": "call1", "name": "submit_quotes", "arguments": arguments if arguments is not None else json.dumps(SUBMISSION), "status": "completed"})
    # SDK construction tolerates omitted optional response metadata.
    return {"id": "resp_test", "object": "response", "created_at": 1, "model": "gpt-6-sol", "status": status, "output": output, "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300}, "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 40}, "total_tokens": 1100}}


def response(**kwargs):
    """Deserialize fixtures through the installed OpenAI SDK."""
    # SDK parsing builds typed output items just as network responses do.
    return openai._models.construct_type(type_=Response, value=payload(**kwargs))


class ProviderTests(unittest.TestCase):
    """Provider choice must never silently change mode or credentials."""

    def test_defaults_and_cross_provider_rejection(self):
        # Each provider has its own first-pass and retry defaults.
        self.assertEqual(select_models("openai"), ("gpt-6-sol", "gpt-6-astra"))
        # Explicit incompatible model names fail before making requests.
        with self.assertRaises(InputError):
            select_models("openai", "claude-sonnet-5")

    def test_missing_credentials(self):
        # An Anthropic key cannot satisfy an OpenAI request.
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "dummy"}, clear=True):
            # Report the missing selected provider variable.
            with self.assertRaisesRegex(InputError, "OPENAI_API_KEY"):
                create_client("openai")

    def test_batch_rejected_before_request(self):
        # Any accidental batch selection must fail before billable work.
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
        # The shared runner enforces the restriction too.
        with self.assertRaisesRegex(InputError, "--live"):
            asyncio.run(run_jobs(client, [job()], False, Usage()))
        # No API call was made.
        client.responses.create.assert_not_called()

    def test_request_preserves_domains_schema_and_cache_prefix(self):
        # Inspect a production worker request.
        params = request_params(job())
        # The full approved-domain lock must reach the hosted tool.
        self.assertEqual(params["tools"][0]["filters"]["allowed_domains"], ["example.com"])
        # Submissions use strict structured function arguments.
        self.assertTrue(params["tools"][1]["strict"])
        # Stable instructions retain an explicit cache boundary.
        self.assertEqual(params["input"][0]["content"][0]["prompt_cache_breakpoint"], {"mode": "explicit"})
        # Provider-specific Anthropic parameters must not leak into Responses.
        self.assertFalse({"thinking", "output_config", "cache_control"} & params.keys())

    def test_domain_limit_fails_closed(self):
        # A large domain list must never be silently truncated.
        worker = job()
        # Exceed the documented OpenAI domain-filter limit.
        worker.tools[0]["allowed_domains"] = [f"d{i}.example" for i in range(101)]
        # No unrestricted fallback is permitted.
        with self.assertRaises(WorkerError):
            request_params(worker)

    def test_empty_domains_fail_closed(self):
        # An empty domain filter cannot permit open-web sourcing.
        worker = job()
        # Simulate a malformed supplier configuration.
        worker.tools[0]["allowed_domains"] = []
        # Fail locally instead of sending an unrestricted request.
        with self.assertRaises(WorkerError):
            request_params(worker)

    def test_api_errors_and_authentication(self):
        # Build SDK errors with local response objects.
        request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
        # Ordinary API failures mark the worker's rows as errored.
        error = openai.BadRequestError("bad request", response=httpx2.Response(400, request=request), body=None)
        # Fake only the provider network boundary.
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=error)))
        # The runner returns an error string in the job's result slot.
        self.assertIsInstance(asyncio.run(run_jobs(client, [job()], True, Usage()))[0], str)
        # Bad credentials must abort the whole run instead of returning empty quotes.
        client.responses.create.side_effect = openai.AuthenticationError("rejected", response=httpx2.Response(401, request=request), body=None)
        # Preserve the SDK's authentication error for the CLI to explain.
        with self.assertRaises(openai.AuthenticationError):
            asyncio.run(run_jobs(client, [job()], True, Usage()))

    def test_usage_has_disjoint_cache_meters_and_search_fees(self):
        # Record one real-shaped response through the adapter.
        worker, usage = job(), Usage()
        # Quotes still go through the existing validator.
        result = handle_response(worker, response(), usage)
        # Cache reads and writes are subsets of input, not additional tokens.
        self.assertEqual((usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens, usage.output_tokens), (500, 200, 300, 100))
        # Only search actions incur the search fee; page opening is tracked separately.
        self.assertEqual((usage.web_searches, usage.web_fetches), (1, 1))
        # Aggregate and per-conversation meters agree.
        self.assertEqual(usage, worker.usage)
        # Output reasoning tokens are already included in output_tokens.
        self.assertAlmostEqual(estimate_cost(usage, "gpt-6-sol"), 0.01279)
        # The returned quote retains its approved product URL.
        self.assertEqual(result[0].quotes[0].url, "https://example.com/608")

    def test_incomplete_and_malformed_output_preserve_usage(self):
        # Truncation and invalid JSON must not produce usable quotes.
        for data in (response(status="incomplete"), response(arguments="{")):
            # Keep the two failures independent.
            worker, usage = job(), Usage()
            # Both failures should be reported as row errors.
            with self.assertRaises(WorkerError):
                handle_response(worker, data, usage)
            # Billable response usage survives the failure.
            self.assertEqual(usage.requests, 1)

    def test_refusal_and_turn_limit(self):
        # Refusal ends a conversation without a workaround retry.
        refused = response(submit=False)
        # Represent the standard Responses refusal content block.
        refused.output.append(SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")]))
        # Refused responses are worker failures.
        with self.assertRaisesRegex(WorkerError, "refused"):
            handle_response(job(), refused, Usage())
        # Repeated non-submission must terminate within the shared turn cap.
        worker, usage = job(), Usage()
        # Earlier turns preserve a replayable history.
        for _ in range(MAX_TURNS - 1):
            self.assertIsNone(handle_response(worker, response(submit=False), usage))
        # The last allowed turn fails predictably.
        with self.assertRaisesRegex(WorkerError, "no submission"):
            handle_response(worker, response(submit=False), usage)

    def test_unapproved_quote_is_rejected(self):
        # Clone the fixture before changing its URL.
        submission = json.loads(json.dumps(SUBMISSION))
        # A valid-shaped quote on another domain must be discarded.
        submission["parts"][0]["quotes"][0]["url"] = "https://unapproved.example/608"
        # The provider adapter uses the same validator as Anthropic.
        result = handle_response(job(), response(arguments=json.dumps(submission)), Usage())
        # Invalid quotes never enter the CBOM or cache as accepted prices.
        self.assertEqual(result[0].quotes, [])

    def test_real_sdk_serialization_and_continuation(self):
        # Requests will be captured by an in-process transport, never sent externally.
        requests = []
        # Respond locally to SDK HTTP requests.
        def transport(request):
            # Decode the actual serialized JSON body.
            requests.append(json.loads(request.content))
            # Force one continuation before a valid submission.
            return httpx2.Response(200, json=payload(submit=len(requests) > 1))
        # Exercise the real SDK and shared runner asynchronously.
        async def run():
            # The mock transport is the only transport available to this client.
            async with openai.AsyncOpenAI(api_key="offline-test", http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(transport))) as client:
                # Return the parsed quotes from the real SDK boundary.
                return await run_jobs(client, [job()], True, Usage())
        # A valid result proves request serialization and response decoding agree.
        self.assertTrue(asyncio.run(run())[0][0].quotes)
        # Search items from the first response must survive into the second request.
        self.assertEqual(len(requests), 2)
        # No provider-side response storage is required for continuation.
        self.assertFalse(requests[1]["store"])
        # Confirm the user-domain restriction survived actual SDK serialization.
        self.assertEqual(requests[1]["tools"][0]["filters"]["allowed_domains"], ["example.com"])

    def test_openai_retry_cache_and_summary_attribution(self):
        # First pass finds nothing; the stronger OpenAI model returns a quote.
        empty = json.dumps({"parts": [{"part_id": "B1", "quotes": [], "notes": "not found"}]})
        # Fake only the network boundary; use real pipeline and adapters.
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=[response(arguments=empty), response()])))
        # A private cache receives the accepted quote.
        cache = {}
        # Run the full first pass and retry sequence.
        summary = asyncio.run(source_bom(client, ROWS, {"currency": "USD", "suppliers": SUPPLIERS}, cache, 0, True, True, "gpt-6-sol", "gpt-6-astra"))
        # The lowest-total rule still selects two units at two dollars each.
        self.assertEqual(summary.rows[0]["extended_price"], "4.00")
        # Retry selection must stay on OpenAI.
        self.assertEqual([call.kwargs["model"] for call in client.responses.create.call_args_list], ["gpt-6-sol", "gpt-6-astra"])
        # The summary must price the actual models, never hardcoded Claude defaults.
        output = io.StringIO()
        # Capture the displayed model labels.
        with contextlib.redirect_stdout(output):
            print_run(summary, Path("cbom.csv"))
        # OpenAI names appear in the report.
        self.assertIn("gpt-6-astra", output.getvalue())
        # Anthropic names do not appear in the OpenAI run summary.
        self.assertNotIn("claude", output.getvalue())
        # Cached quotes can be reused without any provider call.
        cached = asyncio.run(source_bom(client, ROWS, {"currency": "USD", "suppliers": SUPPLIERS}, cache, 7, True, False, "gpt-6-sol", "gpt-6-astra"))
        # Reuse is free and preserves the sourced row.
        self.assertEqual((cached.reused, cached.usage.requests), (1, 0))

    def test_anthropic_dispatch_and_token_discount_unchanged(self):
        # Existing Anthropic jobs must keep their original loop.
        with patch("sourcing_agent.core.runner.run_live", new_callable=AsyncMock, return_value=["ok"]) as run:
            # Select the original model and runner.
            self.assertEqual(asyncio.run(run_jobs(object(), [job("claude-sonnet-5")], True, Usage())), [["ok"]])
            # Exactly one Anthropic conversation was run.
            run.assert_awaited_once()
        # Batch discounts tokens but never search fees.
        usage = Usage(input_tokens=1000, output_tokens=100, web_searches=2)
        # Price the same usage in both Anthropic modes.
        live, batch = cost_by_meter(usage, "claude-sonnet-5"), cost_by_meter(usage, "claude-sonnet-5", True)
        # Search billing is independent of batch discount.
        self.assertEqual(live["searches"], batch["searches"])
        # Token billing retains the existing half-price rule.
        self.assertEqual(live["input"] / 2, batch["input"])

    def test_provider_learning_isolation_and_log_attribution(self):
        # Build sufficient Anthropic history to trigger the pooled profile.
        conv = {"outcome": "submitted", "rows": 1, "suppliers": 1, "usage": {"requests": 1, "input_tokens": 100000, "cache_write_tokens": 0, "cache_read_tokens": 0, "output_tokens": 100000, "web_searches": 1, "web_fetches": 1}, "quoted_rows": 1}
        # Give learning a valid historical first-pass record.
        records = [{"model": "claude-sonnet-5", "batch": False, "error_rows": 0, "shape": [[1, 1]], "actual_cost": 1.2, "conversations": [conv] * 5}]
        # OpenAI estimates must remain assumed until its own history exists.
        self.assertEqual(sourcing_estimator(records)([(1, 1)], "gpt-6-sol", False), sourcing_estimator([])([(1, 1)], "gpt-6-sol", False))
        # Use a private log file for calibration and record assertions.
        with tempfile.TemporaryDirectory() as directory:
            # Historical totals-only fields remain usable.
            log = Path(directory) / "runs.jsonl"
            # Populate only Anthropic history.
            log.write_text(json.dumps(records[0]) + "\n")
            # Anthropic data cannot calibrate OpenAI.
            self.assertIsNone(learned(log, "openai").fit)
            # Append a normalized OpenAI pass using production logging.
            record_pass(log, "test", "first", "gpt-6-sol", False, [(1, 1)], 0, Usage(requests=1, input_tokens=100), Range(0.01, 0.02))
            # Provider attribution is explicit in the appended record.
            self.assertEqual(json.loads(log.read_text().splitlines()[-1])["provider"], "openai")

    def test_cli_comparison_and_invalid_model_without_credentials(self):
        # All estimate operations must work without API keys.
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            # The real fixture BOM uses the real supplier configuration.
            self.assertEqual(main(["estimate", "examples/bom.csv", "--compare"]), 0)
        # Both provider families appear in the comparison.
        self.assertIn("gpt-6-luna", output.getvalue())
        # Reject a cross-provider selection with a clean input error.
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["estimate", "examples/bom.csv", "--provider", "openai", "--model", "claude-sonnet-5"]), 2)


# Allow direct offline test execution.
if __name__ == "__main__":
    unittest.main()
