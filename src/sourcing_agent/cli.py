"""Command line: `sourcing <command>`; CBOM commands from cbom/, supplier commands from suppliers/, `learn` from learning/."""

# Argument parsing.
import argparse
# Progress output.
import logging
# Error output and exit codes.
import sys

# API error types.
import anthropic
# OpenAI credential errors.
import openai

# CBOM commands (run, estimate).
from .cbom import commands as cbom_commands
# Sourcing estimator and assumed token profile, handed to learning.
from .cbom.estimate import ASSUMED, sourcing_estimator
# Error for bad input or a refused action.
from .core.errors import InputError
# Per-user preferences and safe command outcomes.
from .personal import store as memory
# Offline personal preference commands.
from .personal import commands as personal_commands
# Learning command (learn).
from .learning import commands as learning_commands
# Supplier commands (suppliers, discover, trial, candidates).
from .suppliers import commands as supplier_commands


def build_parser() -> argparse.ArgumentParser:
    """Compose command groups without running a use case or creating storage."""
    # Top-level parser.
    parser = argparse.ArgumentParser(prog="sourcing", description="Source a BOM from approved suppliers, and find new suppliers.")
    # Subcommands from both abilities.
    commands = parser.add_subparsers(dest="command", required=True)
    cbom_commands.register(commands)
    supplier_commands.register(commands)
    learning_commands.register(commands, sourcing_estimator, ASSUMED)
    # Personal memory is an offline command.
    personal_commands.register(commands)
    # Return a side-effect-free parser for the CLI and tests.
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch while keeping invalid preferences recoverable."""
    # Folder resolution can report damaged personal preferences during parsing.
    try:
        # Resolve the selected command without creating provider clients.
        args = build_parser().parse_args(argv)
    # Report malformed local preferences as ordinary input errors.
    except (InputError, OSError) as exc:
        # Leave the original profile available for repair.
        print(f"Input error: {exc}", file=sys.stderr)
        # Match the documented input-error exit status.
        return 2
    # Keep execution/error recording separate from parser construction.
    return execute(args)


def execute(args) -> int:
    """Run a parsed command and retain safe outcome labels independently of rendering."""
    # Set a safe outcome even if the handler raises unexpectedly.
    outcome = "unexpected_error"
    # Warnings only from other libraries, on stderr.
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    # Progress lines from this package.
    logging.getLogger("sourcing_agent").setLevel(logging.INFO)
    try:
        # Run the command.
        result = args.handler(args)
        # Preserve row-level failures as a distinct run outcome.
        outcome = "success" if result == 0 else "row_errors"
        # Keep the command's exit status.
        return result
    except (InputError, OSError) as e:
        # Retain a safe failure category for subsequent runs.
        outcome = "input_error"
        # Missing file, bad input, or a refused action.
        print(f"Input error: {e}", file=sys.stderr)
        return 2
    except openai.AuthenticationError:
        # Remember a credential failure without recording a credential.
        outcome = "authentication_error"
        # Key present but rejected by OpenAI.
        print("OpenAI API key was rejected. Check OPENAI_API_KEY.", file=sys.stderr)
        # Match the existing credential-error exit code.
        return 2
    except anthropic.AuthenticationError:
        # Remember a credential failure without recording a credential.
        outcome = "authentication_error"
        # Key present but rejected.
        print("Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    except TypeError as e:
        # No credentials found at all.
        if "authentication" not in str(e):
            raise
        # Remember a credential failure without recording a credential.
        outcome = "authentication_error"
        print("No Anthropic credentials found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2

    finally:
        # Memory inspection itself should remain read-only.
        if args.command not in ("memory", "startup"):
            # A memory-write failure must not hide the command's result.
            try:
                # Arguments and exception messages never enter this event log.
                memory.record_event(args.command, outcome)
            # Report persistence failures without changing the sourcing result.
            except OSError:
                # The user should know that this outcome was not retained.
                print("Could not save the command outcome to personal memory.", file=sys.stderr)


# Allow python -m sourcing_agent.cli.
if __name__ == "__main__":
    sys.exit(main())
