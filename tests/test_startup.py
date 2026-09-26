"""Offline onboarding regressions for isolated profiles and explicit source review."""

# Capture command output without API calls.
import io
# Inspect structured startup reports.
import json
# Isolate profiles and source fixtures.
import tempfile
# Run with the standard-library test runner.
import unittest
# Scope patches and output capture.
from contextlib import ExitStack, redirect_stdout, redirect_stderr
# Build isolated paths.
from pathlib import Path
# Replace only filesystem paths and paid handlers.
from unittest.mock import patch
# Exercise the real CLI dispatch.
from sourcing_agent.cli import main
# Override profile paths for every test.
from sourcing_agent.core import paths
# Simulate a safe operational failure.
from sourcing_agent.core.errors import InputError
# Inspect persisted preferences.
from sourcing_agent.personal.store import load_memory, update_memory
# Test template publication at its filesystem boundary.
from sourcing_agent.core.storage import copy_if_absent


class StartupTests(unittest.TestCase):
    """Startup must preserve user files and require current source acknowledgment."""

    def setUp(self):
        # Register cleanup before creating fixtures.
        self.stack = ExitStack()
        # Release temporary files and patched paths after each test.
        self.addCleanup(self.stack.close)
        # Keep all writes isolated from the user's profile.
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # Represent a fresh user account.
        self.profile = self.root / "profile"
        # Use a local source fixture without network access.
        self.suppliers = self.root / "suppliers.toml"
        # Provide the minimal valid configured supplier list.
        self.suppliers.write_text('currency = "USD"\ncategories = ["other"]\n[[suppliers]]\nname = "Example"\ndomains = ["example.com"]\ncategories = ["other"]\n')
        # Represent discovery references separately from suppliers.
        self.guide = self.root / "sources.md"
        # Content changes must invalidate review.
        self.guide.write_text("Manufacturer directories and buyer reports.")
        # Patch only paths used by personal setup.
        for field, value in {"USER_DATA": self.profile, "MEMORY_PATH": self.profile / "memory.json", "EVENTS_PATH": self.profile / "events.jsonl", "SUPPLIER_SOURCES_PATH": self.guide}.items():
            # Both commands and persistence share these constants.
            self.stack.enter_context(patch.object(paths, field, value))

    def startup(self, *args):
        """Read a successful CLI report."""
        # Keep JSON output separate from test runner output.
        with redirect_stdout(io.StringIO()) as output:
            # Running startup must never require credentials.
            self.assertEqual(main(["startup", "--suppliers", str(self.suppliers), *args]), 0)
        # Return the real command's structured result.
        return json.loads(output.getvalue())

    def test_inspection_has_no_writes_and_shows_sources(self):
        # A new account can inspect everything before making choices.
        report = self.startup()
        # All folder choices are initially outstanding.
        self.assertEqual(report["missing_folders"], ["templates", "boms", "cboms"])
        # Inspection cannot imply source acceptance.
        self.assertTrue(report["sources_review_required"])
        # The user can inspect domains and category coverage.
        self.assertEqual(report["sources"]["suppliers"][0]["domains"], ["example.com"])
        # Startup exposes discovery references as well as suppliers.
        self.assertEqual(report["sources"]["discovery_sources"], self.guide.read_text())
        # Merely looking must not initialize or log a profile.
        self.assertFalse(self.profile.exists())

    def test_initialize_accept_and_preserve_filled_template(self):
        # A single folder is a supported explicit user choice.
        folder = self.root / "sourcing"
        # Initialize all folders without accepting sources implicitly.
        report = self.startup("--templates", str(folder), "--boms", str(folder), "--cboms", str(folder))
        # Initialization must provide the workbook.
        template = folder / "bom_template.xlsx"
        # The copied template is byte-identical to the source.
        self.assertEqual(template.read_bytes(), (paths.PROJECT_ROOT / "templates/bom_template.xlsx").read_bytes())
        # Folder setup is not source approval.
        self.assertTrue(report["startup_required"])
        # Simulate the user filling the workbook.
        template.write_bytes(b"user-filled workbook")
        # Acknowledge exactly the displayed source revision.
        report = self.startup("--accept-sources", report["sources"]["revision"])
        # Both explicit setup steps are now complete.
        self.assertFalse(report["startup_required"])
        # Repeat startup without destroying the filled template.
        self.startup("--templates", str(folder))
        # Personal work survives repeated initialization.
        self.assertEqual(template.read_bytes(), b"user-filled workbook")
        # No generic instructions are seeded into memory.
        self.assertEqual(load_memory()["lessons"], [])

    def test_changed_sources_reject_stale_acceptance(self):
        # Display the revision the user could have reviewed.
        revision = self.startup()["sources"]["revision"]
        # Change the discovery guide before acknowledgment.
        self.guide.write_text("Updated manufacturer directory.")
        # Stale acceptance must be a recoverable input error.
        with redirect_stderr(io.StringIO()):
            # Reject the old revision without creating a profile.
            self.assertEqual(main(["startup", "--suppliers", str(self.suppliers), "--accept-sources", revision]), 2)
        # No stale acknowledgment was saved.
        self.assertFalse(self.profile.exists())
        # Accept the current guide explicitly.
        report = self.startup()
        # Source review can be recorded separately from folder selection.
        self.startup("--accept-sources", report["sources"]["revision"])
        # Supplier-list changes also require review again.
        self.suppliers.write_text(self.suppliers.read_text().replace("example.com", "vendor.example"))
        # The acknowledgment cannot cover newly changed suppliers.
        self.assertTrue(self.startup()["sources_review_required"])

    def test_existing_profile_preserves_preferences_and_corrupt_data(self):
        # Existing personal facts must survive startup.
        update_memory({"boms": self.root}, "My workshop receives deliveries on weekdays.")
        # Changing a different folder must preserve the existing choices.
        self.startup("--cboms", str(self.root / "outputs"))
        # The saved input folder was not reset.
        self.assertEqual(load_memory()["folders"]["boms"], str(self.root))
        # The specific personal fact was not replaced by generic instructions.
        self.assertEqual(load_memory()["lessons"], ["My workshop receives deliveries on weekdays."])
        # Simulate damaged preferences before initialization.
        paths.MEMORY_PATH.write_text("broken")
        # Refuse to overwrite recoverable data.
        with redirect_stderr(io.StringIO()):
            # Surface the error through the CLI.
            self.assertEqual(main(["startup", "--suppliers", str(self.suppliers)]), 2)
        # Keep the original bytes for repair.
        self.assertEqual(paths.MEMORY_PATH.read_text(), "broken")

    def test_command_failures_are_events_not_personal_rules(self):
        # Avoid paid work while exercising operational error retention.
        with patch("sourcing_agent.cbom.commands.cmd_run", side_effect=InputError("private-example")), redirect_stderr(io.StringIO()):
            # Report the existing input-error exit code.
            self.assertEqual(main(["run", "/tmp/missing-bom.csv"]), 2)
        # Events retain safe labels only.
        event = json.loads(paths.EVENTS_PATH.read_text())
        # The outcome supports later review without a personal instruction.
        self.assertEqual(event["outcome"], "input_error")
        # Exception details stay out of stored outcomes.
        self.assertNotIn("private-example", paths.EVENTS_PATH.read_text())
        # Code never invents personal preferences on failure.
        self.assertFalse(paths.MEMORY_PATH.exists())

    def test_missing_template_reopens_setup_without_resetting_review(self):
        # Initialize and acknowledge the current sources.
        folder = self.root / "sourcing"
        # Save the explicit folder choices.
        report = self.startup("--templates", str(folder), "--boms", str(folder), "--cboms", str(folder))
        # Complete source review once.
        self.startup("--accept-sources", report["sources"]["revision"])
        # Simulate a user moving the template away.
        (folder / "bom_template.xlsx").rename(folder / "filled.xlsx")
        # Folder existence alone must not imply startup completion.
        report = self.startup()
        # The missing workbook is a repairable setup step.
        self.assertTrue(report["template_required"])
        # Previously reviewed sources remain accepted.
        self.assertFalse(report["sources_review_required"])
        # Startup remains pending until the template is restored.
        self.assertTrue(report["startup_required"])
        # Reusing the saved destination repairs only the missing file.
        self.assertFalse(self.startup("--templates", str(folder))["startup_required"])

    def test_source_revision_survives_checkout_move(self):
        # Review one supplier snapshot.
        original = self.startup()["sources"]["revision"]
        # Simulate the same supplier file in another checkout.
        relocated = self.root / "relocated.toml"
        # Preserve exact contents while changing only the path.
        relocated.write_bytes(self.suppliers.read_bytes())
        # Point the helper at the moved supplier list.
        self.suppliers = relocated
        # Identical sources do not require a redundant review after a merge.
        self.assertEqual(self.startup()["sources"]["revision"], original)

    def test_failed_template_copy_leaves_no_partial_workbook(self):
        # Keep the template destination separate from its source.
        destination = self.root / "template.xlsx"
        # Simulate a write failure after some bytes were copied.
        def interrupted(original, stream):
            # An incomplete staging file must never be published.
            stream.write(b"partial")
            # Surface the storage failure to setup.
            raise OSError("copy interrupted")
        # Exercise the real staging and cleanup behavior.
        with patch("sourcing_agent.core.storage.copyfileobj", interrupted):
            # Failed initialization must report the error.
            with self.assertRaisesRegex(OSError, "copy interrupted"):
                # Use any source bytes because the copy boundary is controlled.
                copy_if_absent(self.suppliers, destination)
        # An incomplete workbook cannot block future initialization.
        self.assertFalse(destination.exists())
        # The staging file must also have been cleaned up.
        self.assertEqual(set(self.root.iterdir()), {self.suppliers, self.guide})
        # Existing user files need no readable repository template.
        destination.write_bytes(b"filled")
        # A missing source must not prevent preserving the user's file.
        copy_if_absent(self.root / "missing-source", destination)
        # The filled workbook remains untouched.
        self.assertEqual(destination.read_bytes(), b"filled")
