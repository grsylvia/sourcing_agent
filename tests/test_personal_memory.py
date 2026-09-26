"""Offline checks for per-user persistence, routing, and safe error learning."""

# Capture CLI output.
import io
# Inspect persisted JSON.
import json
# Isolate all personal writes.
import tempfile
# Standard test runner.
import unittest
# Scoped mocks and captured output.
from contextlib import ExitStack, redirect_stdout, redirect_stderr
# Filesystem fixtures.
from pathlib import Path
# Replace API work and personal locations.
from unittest.mock import patch
# CLI behavior under test.
from sourcing_agent.cli import main
# Personal memory and path definitions.
from sourcing_agent.core import paths
# Personal preferences have their own package.
from sourcing_agent.personal import store as memory
# Expected user-facing validation error.
from sourcing_agent.core.errors import InputError
# Persist API-pass records in a fresh profile.
from sourcing_agent.learning.runlog import append_run, load_runs


class PersonalMemoryTests(unittest.TestCase):
    """Different profiles must retain independent preferences and learning."""

    def setUp(self):
        # Clean up fixtures and patches even if a test fails.
        self.stack = ExitStack()
        # Register cleanup before making fixtures.
        self.addCleanup(self.stack.close)
        # A fresh profile simulates a new OS account.
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "profile"
        # Every write stays in this test's profile.
        for field, value in {"USER_DATA": self.root, "MEMORY_PATH": self.root / "memory.json", "EVENTS_PATH": self.root / "events.jsonl", "RUN_LOG_PATH": self.root / "run_log.jsonl"}.items():
            # Patch shared path constants used by all command modules.
            self.stack.enter_context(patch.object(paths, field, value))

    def test_first_run_persistence_and_isolation(self):
        # Inspecting memory must not silently confirm defaults.
        with redirect_stdout(io.StringIO()) as output:
            # The command needs no API credentials.
            self.assertEqual(main(["memory"]), 0)
        # A new user must be prompted by the skill.
        self.assertTrue(json.loads(output.getvalue())["setup_required"])
        # Reading memory does not create storage.
        self.assertFalse(self.root.exists())
        # Save three independent locations plus an explicit lesson.
        selected = {k: self.root / k for k in ("templates", "boms", "cboms")}
        # Persist choices as one update.
        memory.update_memory(selected, "My workshop receives deliveries on weekdays.")
        # Repeated verified lessons do not duplicate.
        memory.update_memory({}, "My workshop receives deliveries on weekdays.")
        # Reloaded state matches the user's choices.
        self.assertEqual(memory.folder("boms"), selected["boms"])
        # One lesson survives reload.
        self.assertEqual(len(memory.load_memory()["lessons"]), 1)
        # A different account/profile sees no personal preferences.
        with patch.object(paths, "MEMORY_PATH", self.root / "another-user" / "memory.json"):
            # There is no fallback to someone else's memory.
            self.assertEqual(memory.load_memory(), {"folders": {}, "lessons": []})
        # Returning to the original profile restores its choices.
        self.assertEqual(memory.folder("cboms"), selected["cboms"])

    def test_saved_locations_and_explicit_overrides(self):
        # Use separate folders to catch BOM/CBOM mixups.
        memory.update_memory({"boms": self.root / "inputs", "cboms": self.root / "outputs"})
        # Bare filenames use the saved input folder.
        self.assertEqual(memory.bom_path("part.csv"), self.root / "inputs/part.csv")
        # Bare output filenames use the saved output folder.
        self.assertEqual(memory.cbom_path("part.csv"), self.root / "outputs/part.csv")
        # Explicit relative paths still refer to the working directory.
        self.assertEqual(memory.bom_path("./part.csv"), Path("part.csv"))
        # Absolute paths override personal preferences.
        self.assertEqual(memory.cbom_path("/tmp/part.csv"), Path("/tmp/part.csv"))
        # Tilde expansion works even when quoted by a shell.
        self.assertEqual(memory.bom_path("~/part.csv"), Path.home() / "part.csv")
        # Exercise CLI routing through the real parser without making API calls.
        with patch("sourcing_agent.cbom.commands.cmd_run", return_value=0) as handler:
            # Saved folder selection happens before invoking the pipeline.
            self.assertEqual(main(["run", "part.csv", "--out", "priced.csv"]), 0)
        # The parsed command carries the actual chosen locations.
        self.assertEqual(handler.call_args.args[0].bom, self.root / "inputs/part.csv")
        # Explicit output names are resolved to the saved CBOM folder.
        self.assertEqual(handler.call_args.args[0].out, self.root / "outputs/priced.csv")

    def test_failure_events_do_not_copy_exception_text(self):
        # A deliberate input error includes sensitive-looking text to test exclusion.
        with patch("sourcing_agent.cbom.commands.cmd_run", side_effect=InputError("private-token-example")), redirect_stderr(io.StringIO()):
            # Preserve the existing error code.
            self.assertEqual(main(["run", "/tmp/missing.csv"]), 2)
        # Inspect the local event rather than the terminal error message.
        saved = paths.EVENTS_PATH.read_text()
        # The error category is useful for next-run review.
        self.assertEqual(json.loads(saved)["outcome"], "input_error")
        # Raw errors and paths must not leak into this log.
        self.assertNotIn("private-token-example", saved)
        # Command arguments are excluded as well.
        self.assertNotIn("missing.csv", saved)
        # A subsequent session sees a count of the earlier failure.
        with redirect_stdout(io.StringIO()) as output:
            # Reading stored events makes no API call.
            main(["memory"])
        # Memory surfaces a repeatable failure category.
        self.assertEqual(json.loads(output.getvalue())["command_outcomes"], {"run: input_error": 1})

    def test_new_profile_retains_api_passes(self):
        # Log creation must work before any preference setup.
        append_run(paths.RUN_LOG_PATH, {"run_id": "one", "error_rows": 1})
        # A later pass appends rather than replacing the prior evidence.
        append_run(paths.RUN_LOG_PATH, {"run_id": "two", "error_rows": 0})
        # Both failures and successes remain available to learning.
        self.assertEqual([r["run_id"] for r in load_runs(paths.RUN_LOG_PATH)], ["one", "two"])

    def test_corrupt_memory_is_not_overwritten(self):
        # Seed a valid profile first.
        memory.update_memory({"boms": self.root})
        # Simulate a damaged disk file.
        paths.MEMORY_PATH.write_text("broken")
        # Refuse to replace damaged personal data silently.
        with self.assertRaises(InputError):
            # Even an explicit new note must preserve recoverable old contents.
            memory.update_memory({}, "new lesson")
        # Leave the damaged data available for repair.
        self.assertEqual(paths.MEMORY_PATH.read_text(), "broken")

    def test_cli_reports_corrupt_preferences_without_overwriting(self):
        # Preserve malformed personal data for repair.
        memory.update_memory({"boms": self.root})
        # Folder resolution happens during CLI parsing.
        paths.MEMORY_PATH.write_text("broken")
        # Bad preferences should be an input error rather than a traceback.
        with redirect_stderr(io.StringIO()) as output:
            # A bare filename requires saved-folder resolution.
            self.assertEqual(main(["estimate", "part.csv"]), 2)
        # Explain the recoverable problem to the caller.
        self.assertIn("personal memory", output.getvalue())
        # Never reset a user's corrupt profile silently.
        self.assertEqual(paths.MEMORY_PATH.read_text(), "broken")
