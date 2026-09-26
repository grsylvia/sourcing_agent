"""Offline discovery integration tests for maintained outside-source guidance."""

# Application-assigned evidence dates.
import datetime
# Capture CLI reports.
import io
# Temporary guides and registries.
import tempfile
# Standard test runner.
import unittest
# Output capture.
from contextlib import redirect_stdout
# Paths for isolated files.
from pathlib import Path
# CLI arguments.
from types import SimpleNamespace
# Replace network and project paths during tests.
from unittest.mock import patch

# CLI discovery and registry reports.
from sourcing_agent.suppliers.commands import cmd_candidates, cmd_discover
# Evidence validation and source guidance.
from sourcing_agent.suppliers.evidence import evidence_flags, normalize_evidence, source_guide
# Normal application errors.
from sourcing_agent.core.errors import InputError
# Registry persistence.
from sourcing_agent.suppliers.registry import load_registry
# Scout prompt, schema, and parsing.
from sourcing_agent.suppliers.scout import build_candidates_tool, parse_candidates, scout_job, scout_usage
# Free screening gates.
from sourcing_agent.suppliers.screen import screen


def observation(url='https://forum.example/topic', source_type='forum', signal='supporting'):
    """One external account with source provenance."""
    # Match the submission schema.
    return {'url': url, 'source_type': source_type, 'published_at': '2025-02-01', 'basis': 'firsthand', 'signal': signal, 'summary': 'Received the specified bearings; delivery matched the promise.'}


def submission(evidence=None):
    """One priced supplier, independent of its outside reputation."""
    # Two own-domain priced listings satisfy the existing store screen.
    return {'candidates': [{'name': 'New Bearings', 'domain': 'new.example', 'product_pages': [{'url': f'https://new.example/{part}', 'part': part, 'price_seen': '$2 each'} for part in ('625', '6812')], 'prices_visible_without_login': True, 'price_breaks_or_packs_shown': True, 'ships_to_us': True, 'contact_url': 'https://new.example/contact', 'returns_url': 'https://new.example/returns', 'reason': 'Matches the required bearing sizes.', 'external_evidence': evidence or [], 'external_notes': 'Certificate check not applicable.'}]}


# Exercise the actual prompt, parser, screening and command paths.
class SupplierEvidenceTests(unittest.TestCase):
    # The live job uses maintained guidance while allowing external source sites.
    def test_guide_and_caps(self):
        # Use a temporary guide to prove there is no stale embedded copy.
        with tempfile.TemporaryDirectory() as directory:
            # Path to a custom maintained guide.
            path = Path(directory) / 'sources.md'
            # Seed short guidance.
            path.write_text('Use independent machining forums.')
            # Redirect the runtime guide loader.
            with patch('sourcing_agent.core.paths.SUPPLIER_SOURCES_PATH', path):
                # Build the real scout job.
                job = scout_job('bearings', [], [], {'approved.example'})
                # Guidance reaches the API message.
                self.assertIn(path.read_text(), job.messages[0]['content'])
                # Search and fetch costs remain bounded.
                self.assertEqual([job.tools[0]['max_uses'], job.tools[1]['max_uses']], [8, 6])
                # Forums and review sites remain available outside the supplier allowlist.
                self.assertNotIn('reddit.com', job.tools[0]['blocked_domains'])
                # Record the estimate with short guidance.
                before = scout_usage(False).cache_write_tokens
                # Grow the guide to check its cost is included.
                path.write_text(path.read_text() + ' More guidance.' * 100)
                # Larger guidance increases the prompt estimate.
                self.assertGreater(scout_usage(False).cache_write_tokens, before)
                # Empty guides must fail before paid requests.
                path.write_text('')
                # Missing configuration is visible to the CLI.
                with self.assertRaises(InputError):
                    # Exercise the loader used by both estimates and jobs.
                    source_guide()

    # Self-hosted, malformed and duplicate references cannot inflate outside evidence.
    def test_normalization(self):
        # Include duplicate fragments, unsafe schemes and supplier testimonials.
        items = [observation(), observation('https://forum.example/topic#reply'), observation('https://new.example/reviews'), observation('https://www.new.example/testimonials'), observation('javascript:alert(1)'), observation('https://user:pass@forum.example/topic'), observation('https://[broken'), observation('https://review.example/page') | {'published_at': 'yesterday'}]
        # Normalize the submitted observations.
        evidence = normalize_evidence(items, 'new.example')
        # Only two distinct usable outside pages remain.
        self.assertEqual(len(evidence), 2)
        # The current date comes from code.
        self.assertEqual(evidence[0]['checked_at'], datetime.date.today().isoformat())
        # An unknown publication date remains unknown.
        self.assertEqual(evidence[1]['published_at'], '')

    # Duplicate URLs retain conflicting findings without inflating source coverage.
    def test_duplicate_concern_survives(self):
        # An adverse reply may follow a positive account on the same thread.
        adverse = observation('https://forum.example/topic#reply', signal='concern') | {'summary': 'A second buyer reports a refund dispute.'}
        # Normalize both observations to one source page.
        evidence = normalize_evidence([observation(), adverse], 'new.example')
        # Count the thread only once.
        self.assertEqual(len(evidence), 1)
        # Keep the adverse finding visible.
        self.assertEqual(evidence[0]['signal'], 'concern')
        # Preserve both accounts for review.
        self.assertIn(adverse['summary'], evidence[0]['summary'])

    # Reputation findings are review flags, not proof of qualification or disqualification.
    def test_flags_and_screening(self):
        # An own-domain priced supplier can be trialed with no external reviews.
        candidate = parse_candidates(submission(), 'bearings', set())[0]
        # Run the real free screen with a known domain age.
        screen(candidate, 10)
        # No-review status never blocks a trial.
        self.assertEqual(candidate.verdict, 'trial')
        # Missing coverage is visible.
        self.assertIn('outside verification incomplete', ' '.join(candidate.reasons))
        # Independent forum and business review observations complete the coverage.
        evidence = normalize_evidence([observation(), observation('https://reviews.example/store', 'business_review', 'concern')], 'new.example')
        # A concern is retained even with complete coverage.
        self.assertEqual(evidence_flags(evidence), ['outside concern reported; review evidence'])
        # Adverse evidence does not automatically screen out a valid store.
        candidate.external_evidence = evidence
        # Re-screen with the observations present.
        self.assertEqual(screen(candidate, 10).verdict, 'trial')
        # Seller promotion cannot satisfy independent coverage.
        evidence[0]['basis'] = 'seller_claim'
        # A seller-authored forum post is not a firsthand customer report.
        self.assertIn('incomplete', evidence_flags(evidence)[0])

    # New fields remain compatible with old submissions and strict API schemas.
    def test_schema_and_legacy(self):
        # Inspect the strict candidate schema.
        schema = build_candidates_tool()['input_schema']['properties']['candidates']['items']
        # Every object property is required under strict submission.
        self.assertEqual(set(schema['required']), set(schema['properties']))
        # Outside records follow the same strict schema rule.
        outside = schema['properties']['external_evidence']['items']
        # Enforce consistency without making API requests.
        self.assertEqual(set(outside['required']), set(outside['properties']))
        # Simulate a candidate saved by the old worker.
        old = submission()
        # Remove new fields to exercise parser compatibility.
        del old['candidates'][0]['external_evidence']
        # Older workers did not record check limits either.
        del old['candidates'][0]['external_notes']
        # Legacy submissions stay usable and explicitly unverified.
        self.assertEqual(parse_candidates(old, 'bearings', set())[0].external_evidence, [])

    # Discovery persists evidence through the normal registry and displays it after reload.
    def test_discovery_round_trip(self):
        # Isolate all registry mutations from the real project.
        with tempfile.TemporaryDirectory() as directory:
            # Candidate registry for this mocked run.
            registry_path = Path(directory) / 'registry.json'
            # Minimal valid category and approved suppliers.
            config = {'categories': ['bearings'], 'currency': 'USD', 'suppliers': []}
            # CLI inputs for live discovery.
            args = SimpleNamespace(suppliers=Path('unused.toml'), cboms=[], category=['bearings'], live=True, estimate=False)
            # Mock only the API boundary; use real job parsing and screening.
            async def fake_scouts(jobs, live, usage):
                # Supply one external observation through the real parser.
                return [jobs[0].parse(submission([observation()]))]
            # Redirect external dependencies and registry output.
            with patch('sourcing_agent.core.paths.REGISTRY_PATH', registry_path), patch('sourcing_agent.suppliers.commands.load_suppliers', return_value=config), patch('sourcing_agent.suppliers.commands.read_cboms', return_value=[]), patch('sourcing_agent.suppliers.commands.run_scouts', fake_scouts), patch('sourcing_agent.suppliers.commands.rdap_age_years', return_value=10), redirect_stdout(io.StringIO()) as output:
                # Execute the real discovery command.
                self.assertEqual(cmd_discover(args), 0)
                # Verify persistence via the normal reader.
                saved = load_registry(registry_path)['new.example']
                # The collected source and date survive serialization.
                self.assertEqual(saved['external_evidence'][0]['url'], observation()['url'])
                # No approval is implied by outside evidence.
                self.assertEqual(saved['status'], 'proposed')
                # Reload and display the candidate through the real listing command.
                self.assertEqual(cmd_candidates(SimpleNamespace(approve=None, reject=None)), 0)
                # Both discovery and later registry review show the evidence link.
                self.assertEqual(output.getvalue().count(observation()['url']), 2)
                # Check limits remain visible to the user.
                self.assertIn('Certificate check not applicable.', output.getvalue())


# Allow direct execution.
if __name__ == '__main__':
    # Run the offline regression suite.
    unittest.main()
