"""Completed valid quotes survive an interrupted escalation pass."""

# Exercise the real asynchronous pipeline.
import asyncio
# Validate the cached quote date.
import datetime
# Run offline regression tests.
import unittest
# Replace only the paid pass boundary.
from unittest.mock import patch
# Inspect production cache identities and records.
from sourcing_agent.cbom.cache import lookup, quote_key
# Run production cache and retry orchestration.
from sourcing_agent.cbom.pipeline import source_bom
# Validated worker result fixtures.
from sourcing_agent.cbom.quotes import PartQuotes, PriceBreak, Quote


class QuoteRetentionTests(unittest.TestCase):
    """Successful first-pass work must remain reusable after a retry outage."""

    def test_completed_quotes_survive_retry_interruption(self):
        # Supply two distinct cache identities, one sourced and one unresolved.
        rows = [{"part_id": pid, "category": "other", "quantity": "1", "description": pid, "spec": "", "mfr_part_number": ""} for pid in ("found", "missing")]
        # Use one approved fixture supplier.
        config = {"currency": "USD", "suppliers": [{"name": "Vendor", "domains": ["vendor.example"], "categories": ["other"]}]}
        # A successful quote uses the approved fixture domain.
        quote = Quote("found", "Vendor", "SKU", "https://vendor.example/item", 1, 1, [PriceBreak(1, 2)], "matches")
        # Track pass order without relying on a specific model name.
        calls = []
        # Keep the production cache observable after interruption.
        cache = {}

        async def completed_then_interrupted(client, batch, config, live, model, usage):
            # Only the unresolved row should reach escalation.
            calls.append([row["part_id"] for row in batch])
            # Simulate an outage after the first pass has completed.
            if len(calls) > 1:
                # The caller must still see the retry failure.
                raise RuntimeError("retry interrupted")
            # Return one valid price and one explicit not-found result.
            return {"found": PartQuotes("found", [quote]), "missing": PartQuotes("missing")}, {}, []

        # Replace network work while preserving the real retry policy.
        with patch("sourcing_agent.cbom.pipeline.source_rows", completed_then_interrupted):
            # The pipeline must propagate the interruption.
            with self.assertRaisesRegex(RuntimeError, "retry interrupted"):
                # Both checkouts expose the same basic orchestration contract.
                asyncio.run(source_bom(None, rows, config, cache, 7, True))
        # Escalation includes only the missing row.
        self.assertEqual(calls, [["found", "missing"], ["missing"]])
        # The completed valid quote remains reusable after the exception.
        hit = lookup(cache, quote_key(rows[0], config["suppliers"]), 7, datetime.date.today())
        # Verify actual price preservation rather than merely cache existence.
        self.assertEqual(hit.quotes[0].price_breaks[0].pack_price, 2)
        # Unresolved rows remain eligible for later sourcing.
        self.assertEqual(len(cache), 1)
