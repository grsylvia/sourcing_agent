"""Offline service contracts: paid evidence survives failures and cached work stays free."""

# Execute the real asynchronous orchestration.
import asyncio
# Cache timestamps.
import datetime
# Isolated fixture files.
import tempfile
# Standard test runner.
import unittest
# Fixture paths.
from pathlib import Path
# Replace only paid work and provider construction.
from unittest.mock import AsyncMock, MagicMock, patch
# Real input and cache formats.
from sourcing_agent.cbom.bom import load_bom, write_cbom
# Real quote cache keys and snapshots.
from sourcing_agent.cbom.cache import load_cache, lookup, quote_key, save_cache, store
# Real quote records and price selection.
from sourcing_agent.cbom.quotes import PartQuotes, Quote, PriceBreak
# Application entry point.
from sourcing_agent.cbom.service import run_sourcing
# Real supplier configuration.
from sourcing_agent.core.config import load_suppliers
# Durable pass records.
from sourcing_agent.learning.runlog import load_runs
# Persistence failure behavior.
from sourcing_agent.core.storage import write_json, read_jsonl


class SourcingServiceTests(unittest.TestCase):
    """Verify the service's observable file and learning contracts without an API."""

    def setUp(self):
        # Give every test independent input and output files.
        self.temp = tempfile.TemporaryDirectory()
        # Cleanup even after assertion failures.
        self.addCleanup(self.temp.cleanup)
        # All writes stay outside the user's profile.
        self.root = Path(self.temp.name)
        # Reuse checked-in valid input schemas.
        self.bom = Path('examples/bom.csv')
        # Real supplier configuration exercises production validation.
        self.suppliers = Path('suppliers.toml')
        # Personal runtime artifacts stay in the fixture.
        self.cache, self.out, self.log = [self.root / name for name in ('cache.json', 'out.csv', 'runs.jsonl')]
        # Decode input rows to create valid cached and fresh results.
        self.config = load_suppliers(self.suppliers)
        # Quantity and categories come from the real example BOM.
        self.rows = load_bom(self.bom, self.config['categories'])

    def quotes(self, rows):
        """Return one inexpensive approved quote per input row."""
        # Keep the record shape identical to validated worker output.
        return {row['part_id']: PartQuotes(row['part_id'], [Quote(row['part_id'], 'Example', 'SKU', 'https://example.com/item', 1, 1, [PriceBreak(1, 2.0)], 'matches')], quoted_at=datetime.date.today().isoformat()) for row in rows}

    async def fresh(self, client, rows, config, live, model, usage):
        """Fake only paid worker execution while retaining real orchestration."""
        # Ensure the pass counts as metered work.
        usage.requests = 1
        # Supply a small actual cost for learning.
        usage.input_tokens = 100
        # Validated quote facts, no worker errors, and one conversation record.
        return self.quotes(rows), {}, [{'quoted_rows': len(rows), 'outcome': 'submitted', 'rows': len(rows), 'suppliers': 1, 'usage': {'requests': 1}}]

    def client(self):
        """A local asynchronous client context with no network methods."""
        # The worker boundary is mocked independently.
        client = MagicMock()
        # Returning a plain object ensures no real SDK is accidentally used.
        client.__aenter__ = AsyncMock(return_value=object())
        # Match an ordinary provider context lifecycle.
        client.__aexit__ = AsyncMock(return_value=False)
        # Share the context with the service.
        return client

    def run_service(self):
        """Execute the production service with isolated storage."""
        # Use real planning, orchestration, learning, and CSV export.
        return asyncio.run(run_sourcing(self.bom, self.suppliers, self.out, self.cache, live=True, log_path=self.log))

    def test_export_failure_retains_paid_learning_and_quotes(self):
        # Fail only at the final output boundary.
        with patch('sourcing_agent.cbom.service.create_client', return_value=self.client()), patch('sourcing_agent.cbom.pipeline.source_rows', self.fresh), patch('sourcing_agent.cbom.service.write_cbom', side_effect=OSError('output unavailable')):
            # The caller still learns that export failed.
            with self.assertRaisesRegex(OSError, 'output unavailable'):
                # Paid work has already completed at this point.
                self.run_service()
        # A completed first pass survives the failed export.
        self.assertEqual([r['pass'] for r in load_runs(self.log)], ['first'])
        # Its actual metering remains available to later estimates.
        self.assertGreater(load_runs(self.log)[0]['actual_cost'], 0)
        # Successful prices remain reusable without paying again.
        self.assertTrue(self.cache.exists())

    def test_cache_only_needs_no_provider_client(self):
        # Populate the user's cache with quotes at today's date.
        cached = {}
        # Reuse actual identity keys for each BOM row.
        for row in self.rows:
            # Cache records include the real supplier configuration identity.
            store(cached, quote_key(row, self.config['suppliers']), self.quotes([row])[row['part_id']])
        # Write the same cache format used by normal runs.
        save_cache(self.cache, cached)
        # Any attempt to construct a provider client would fail this test.
        with patch('sourcing_agent.cbom.service.create_client', side_effect=AssertionError('unexpected API client')):
            # Export entirely from stored validated prices.
            summary, lines = self.run_service()
        # Every input row was reused at zero request cost.
        self.assertEqual((summary.reused, summary.usage.requests), (len(self.rows), 0))
        # Cached reuse is not a new paid pass for supplier pruning or calibration.
        self.assertEqual(lines, [])
        # No fabricated pass records were created.
        self.assertFalse(self.log.exists())
        # A real CBOM was produced.
        self.assertTrue(self.out.exists())

    def test_first_pass_survives_retry_failure(self):
        # A partially successful first pass still causes escalation for its gaps.
        async def empty(client, rows, config, live, model, usage):
            # The retry fails before completing its pass.
            if model != 'claude-sonnet-5':
                # Simulate a provider-wide outage during the retry.
                raise RuntimeError('retry interrupted')
            # The first pass still incurred billable work.
            usage.requests, usage.input_tokens = 1, 100
            # Keep one usable quote while the remaining rows require a retry.
            parts = {row['part_id']: PartQuotes(row['part_id']) for row in rows}
            # The completed price must survive the later retry interruption.
            parts.update(self.quotes(rows[:1]))
            # Return the complete first-pass observations.
            return parts, {}, []
        # Exercise the actual first-pass observer before escalation starts.
        with patch('sourcing_agent.cbom.service.create_client', return_value=self.client()), patch('sourcing_agent.cbom.pipeline.source_rows', empty):
            # Propagate the interruption to the caller.
            with self.assertRaisesRegex(RuntimeError, 'retry interrupted'):
                # The first pass must already have been persisted.
                self.run_service()
        # Retain the completed first pass without inventing retry evidence.
        self.assertEqual([r['pass'] for r in load_runs(self.log)], ['first'])
        # The service must persist prices as well as learning after interruption.
        cached = load_cache(self.cache)
        # The successful first row remains reusable at its actual quote date.
        hit = lookup(cached, quote_key(self.rows[0], self.config['suppliers']), 7, datetime.date.today())
        # Avoid paying again for a completed successful quote.
        self.assertIsNotNone(hit)
        # Failed or not-found rows must not become cache hits.
        self.assertEqual(len(cached), 1)

    def test_successful_retry_logs_each_pass_once(self):
        # First-pass absence and successful escalation must be separate observations.
        async def retry(client, rows, config, live, model, usage):
            # Both passes incur real usage in this fixture.
            usage.requests, usage.input_tokens = 1, 100
            # The default model finds nothing on its first attempt.
            if model == 'claude-sonnet-5':
                # Preserve one explicit empty result per requested row.
                return {row['part_id']: PartQuotes(row['part_id']) for row in rows}, {}, []
            # The retry supplies usable prices for export.
            return self.quotes(rows), {}, []
        # Replace only provider creation and paid pass execution.
        with patch('sourcing_agent.cbom.service.create_client', return_value=self.client()), patch('sourcing_agent.cbom.pipeline.source_rows', retry):
            # Run both passes through the production service.
            summary, lines = self.run_service()
        # Rendering must not cause duplicate logging.
        self.assertEqual([r['pass'] for r in load_runs(self.log)], ['first', 'escalation'])
        # Both metered passes return displayable comparisons.
        self.assertEqual(len(lines), 2)
        # Every retry result reaches the exported CBOM.
        self.assertTrue(all(row['status'] == 'sourced' for row in summary.rows))

    def test_failed_snapshots_preserve_previous_files(self):
        # Start with a usable snapshot.
        self.out.write_text('previous output')
        # JSON serialization fails after opening the staged file.
        with self.assertRaises(TypeError):
            # Arbitrary objects are not JSON data.
            write_json(self.out, {'bad': object()})
        # The original destination must remain intact.
        self.assertEqual(self.out.read_text(), 'previous output')
        # CSV serialization can also fail partway through output.
        with self.assertRaises(ValueError):
            # Extra columns violate the output schema.
            write_cbom(self.out, [{'unknown_column': 'bad'}])
        # The previous export still survives.
        self.assertEqual(self.out.read_text(), 'previous output')
        # No failed temporary snapshots remain alongside it.
        self.assertEqual(list(self.root.iterdir()), [self.out])

    def test_damaged_history_keeps_intact_objects(self):
        # Interrupted and invalid lines may coexist with good observations.
        self.log.write_text('{"pass":"first"}\nnull\n[]\nbroken\n{"pass":"escalation"}\n')
        # Non-object lines cannot crash downstream field access.
        self.assertEqual([r['pass'] for r in read_jsonl(self.log)], ['first', 'escalation'])
