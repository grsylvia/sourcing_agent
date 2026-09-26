"""Offline regression tests for supplier pruning and evidence collection."""

# Async pipeline exercises without network calls.
import asyncio
# Temporary run logs.
import tempfile
# Standard-library test runner.
import unittest
# File paths for isolated logs.
from pathlib import Path
# Replace paid API execution with deterministic submissions.
from unittest.mock import patch

# Usage for the pipeline and logger.
from sourcing_agent.core.agent import Usage
# Cost bounds for logging.
from sourcing_agent.core.pricing import Range
# Fresh sourcing pipeline.
from sourcing_agent.cbom.pipeline import source_rows
# Submission parsing and tool schema.
from sourcing_agent.cbom.worker import build_submit_tool, parse_submission
# Persistent learning records.
from sourcing_agent.learning.runlog import load_runs, record_pass
# Shared failure policy.
from sourcing_agent.learning.suppliers import supplier_history
# CBOM win counts must not cause pruning.
from sourcing_agent.suppliers.wins import supplier_wins

# Small approved supplier fixture.
SUPPLIER = {"name": "Vendor", "domains": ["vendor.example"], "categories": ["other"]}
# History identity includes the approved domains.
KEY = ("other", "Vendor", ("vendor.example",))


def record(identity, status="no_quote", count=5, name="first"):
    """Construct one pass with explicit supplier evidence."""
    # Independent rows within one sourcing run.
    outcomes = [{"part_id": str(i), "supplier": "Vendor", "domains": ["vendor.example"], "status": status, "reason": "Searched; no matching listing", "won": False} for i in range(count)]
    # Minimal pruning input.
    return {"run_id": str(identity), "pass": name, "conversations": [{"category": "other", "supplier_outcomes": outcomes}]}


# Failure policy regressions.
class SupplierLearningTests(unittest.TestCase):
    # Independent first passes are required.
    def test_repeated_failures_and_duplicates(self):
        # Large single-run samples still cannot qualify.
        self.assertFalse(supplier_history([record(1, count=100)])[KEY]["drop_candidate"])
        # Duplicated logs cannot manufacture runs.
        self.assertFalse(supplier_history([record(1)] * 3)[KEY]["drop_candidate"])
        # Three sufficiently sampled runs qualify.
        self.assertTrue(supplier_history([record(i) for i in range(3)])[KEY]["drop_candidate"])

    # Incomplete observations and successful losing quotes interrupt failures.
    def test_streak_resets(self):
        # Each kind of inconclusive or successful run prevents pruning.
        for status, count in [("quoted", 5), ("error", 5), ("not_checked", 5), ("no_quote", 4)]:
            # Two failures followed by an interruption and one failure stay below threshold.
            with self.subTest(status=status, count=count):
                # A quoted outcome has zero wins and still proves success.
                history = supplier_history([record(1), record(2), record(3, status, count), record(4)])
                # Only the last run belongs to the current streak.
                self.assertEqual(history[KEY]["failed_runs"], 1)

    # Retries and trials cannot inflate independent run evidence.
    def test_retries_trials_and_legacy(self):
        # Legacy totals and failed retry passes cannot add failing runs.
        history = supplier_history([{}, record(1), record(2, name="escalation"), record(3, name="trial")])
        # Only the initial first pass counts.
        self.assertEqual(history[KEY]["failed_runs"], 1)
        # A successful retry resets an otherwise qualifying streak.
        history = supplier_history([record(i) for i in range(3)] + [record(4, "quoted", name="escalation")])
        # Success keeps the supplier.
        self.assertFalse(history[KEY]["drop_candidate"])
        # Winner-only CBOMs never establish failures.
        rows = [{"status": "sourced", "category": "other", "vendor": "Someone else"}] * 100
        # No explicit search evidence means no drop recommendation.
        self.assertFalse(supplier_wins(rows, [SUPPLIER])[0].drop_candidate)

    # Worker parsing must preserve explicit unknown and failed search distinctions.
    def test_parser_evidence(self):
        # Minimal row fixture.
        rows = [{"part_id": "1"}]
        # Missing search reports cannot be inferred from empty quotes.
        result = parse_submission({"parts": [{"part_id": "1", "quotes": [], "notes": "none"}]}, rows, [SUPPLIER])[0]
        # The supplier is unknown, not failed.
        self.assertEqual(result.supplier_outcomes[0]["status"], "not_checked")
        # New API schema requires explicit reports.
        schema = build_submit_tool(["Vendor"])["input_schema"]["properties"]["parts"]["items"]
        # The schema guarantees the report field for new workers.
        self.assertIn("supplier_outcomes", schema["required"])
        # Explicit failed searches are retained.
        submission = {"parts": [{"part_id": "1", "quotes": [], "notes": "none", "supplier_outcomes": [{"supplier": "Vendor", "status": "no_quote", "reason": "No matching listing after search"}]}]}
        # The parser retains reported unsuccessful searches.
        self.assertEqual(parse_submission(submission, rows, [SUPPLIER])[0].supplier_outcomes[0]["status"], "no_quote")

    # Quote validation must override contradictory worker outcome labels.
    def test_quotes_and_domain_identity(self):
        # A quote on the approved domain with valid price data.
        quote = {"vendor": "Vendor", "vendor_part_number": "A", "url": "https://vendor.example/A", "pack_size": 1, "min_order_packs": 1, "price_breaks": [{"min_packs": 1, "pack_price": 10}], "match_notes": "Exact"}
        # Contradictory no-quote status must not turn a valid quote into a failure.
        part = {"part_id": "1", "quotes": [quote], "notes": "", "supplier_outcomes": [{"supplier": "Vendor", "status": "no_quote", "reason": "No result"}]}
        # Parse a valid submission.
        result = parse_submission({"parts": [part]}, [{"part_id": "1"}], [SUPPLIER])[0]
        # Validation proves success regardless of reported status.
        self.assertEqual(result.supplier_outcomes[0]["status"], "quoted")
        # Change the quote to an unapproved domain.
        quote["url"] = "https://unapproved.example/A"
        # Rejected quotes must not become evidence for pruning.
        result = parse_submission({"parts": [part]}, [{"part_id": "1"}], [SUPPLIER])[0]
        # Rejection is recorded separately from an unsuccessful search.
        self.assertEqual(result.supplier_outcomes[0]["status"], "error")
        # A changed supplier domain must start a new history.
        changed = SUPPLIER | {"domains": ["new.example"]}
        # Past failures on different domains cannot justify removal.
        self.assertFalse(supplier_wins([], [changed], [record(i) for i in range(3)])[0].drop_candidate)

    # Exercise worker batches through pipeline, persistent logging, and aggregation.
    def test_pipeline_logging(self):
        # Five rows span three worker conversations in one run.
        rows = [{"part_id": str(i), "category": "other", "quantity": "1"} for i in range(5)]
        # Approved category configuration.
        config = {"currency": "USD", "suppliers": [SUPPLIER]}
        # Usage proves that the pass made requests.
        usage = Usage(requests=1)
        # Mock completed searches without calling the API.
        async def fake_jobs(client, jobs, live, usage):
            # Return each batch via the real submission parser.
            return [job.parse({"parts": [{"part_id": row["part_id"], "quotes": [], "notes": "none", "supplier_outcomes": [{"supplier": "Vendor", "status": "no_quote", "reason": "No matching priced listing"}]} for row in rows]}) for job in jobs]
        # Run the normal source_rows path with mocked API execution.
        with patch("sourcing_agent.cbom.pipeline.run_jobs", fake_jobs):
            # Collect the same conversations the CLI logs.
            parts, errors, conversations = asyncio.run(source_rows(None, rows, config, True, "claude-sonnet-5", usage))
        # All five outcomes survive batching.
        self.assertEqual(sum(len(c["supplier_outcomes"]) for c in conversations), 5)
        # Use an isolated append-only log.
        with tempfile.TemporaryDirectory() as directory:
            # Temporary learning file.
            path = Path(directory) / "runs.jsonl"
            # Persist the actual pipeline conversation data.
            record_pass(path, "bom.csv", "first", "claude-sonnet-5", False, [(2, 1), (2, 1), (1, 1)], 0, usage, Range(0, 1), conversations=conversations)
            # Three conversations are exactly one independent run.
            self.assertEqual(supplier_history(load_runs(path))[KEY]["failed_runs"], 1)


# Allow direct execution as well as discovery.
if __name__ == "__main__":
    # Run the offline suite.
    unittest.main()
