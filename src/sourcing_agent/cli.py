"""Command line: `sourcing <command>`; CBOM commands from cbom/, supplier commands from suppliers/, `learn` from learning/."""

# Argument parsing.
import argparse
# Progress output.
import logging
# Error output and exit codes.
import sys

# API error types.
import anthropic

# CBOM commands (run, estimate).
from .cbom import commands as cbom_commands
# Sourcing estimator and assumed token profile, handed to learning.
from .cbom.estimate import ASSUMED, sourcing_estimator
# Error for bad input or a refused action.
from .core.errors import InputError
# Learning command (learn).
from .learning import commands as learning_commands
# Supplier commands (suppliers, discover, trial, candidates).
from .suppliers import commands as supplier_commands
# Offline personal preferences and startup.
from .personal import commands as personal_commands
# Safe outcomes live separately from user preferences.
from .personal.store import record_event


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the chosen command, and map input and credential errors to exit code 2."""
    # Top-level parser.
    parser = argparse.ArgumentParser(prog="sourcing", description="Source a BOM from approved suppliers, and find new suppliers.")
    # Subcommands from both abilities.
    commands = parser.add_subparsers(dest="command", required=True)
    cbom_commands.register(commands)
    supplier_commands.register(commands)
    learning_commands.register(commands, sourcing_estimator, ASSUMED)
    # Register free personal setup commands.
    personal_commands.register(commands)
    # Parse the command line.
    args = parser.parse_args(argv)
    # Warnings only from other libraries, on stderr.
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    # Progress lines from this package.
    logging.getLogger("sourcing_agent").setLevel(logging.INFO)
    # Retain unexpected failures without storing exception text.
    outcome = "unexpected_error"
    try:
        # Run the command.
        result = args.handler(args)
        # Preserve unsuccessful row outcomes in the local event history.
        outcome = "success" if result == 0 else "row_errors"
        # Return the command's original exit code.
        return result
    except (InputError, OSError) as e:
        # Record only the failure class, without paths or credentials.
        outcome = "input_error"
        # Missing file, bad input, or a refused action.
        print(f"Input error: {e}", file=sys.stderr)
        return 2
    except anthropic.AuthenticationError:
        # Keep credential values out of the outcome log.
        outcome = "authentication_error"
        # Key present but rejected.
        print("Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    except TypeError as e:
        # No credentials found at all.
        if "authentication" not in str(e):
            raise
        # Retain the credential failure category only.
        outcome = "authentication_error"
        print("No Anthropic credentials found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    # Outcome retention is shared code behavior, never a user preference.
    finally:
        # Inspection and setup do not add noise to operational history.
        if args.command not in ("memory", "startup"):
            # A logging failure must not replace the command's result.
            try:
                # Persist safe fixed labels only.
                record_event(args.command, outcome)
            # Read-only or full storage should leave the result usable.
            except OSError:
                # Disclose that the outcome was not retained.
                print("Could not save the command outcome.", file=sys.stderr)


# Allow python -m sourcing_agent.cli.
if __name__ == "__main__":
    sys.exit(main())
