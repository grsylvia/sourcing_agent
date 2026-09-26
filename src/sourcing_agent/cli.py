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
# Learning command (learn).
from .learning import commands as learning_commands
# Supplier commands (suppliers, discover, trial, candidates).
from .suppliers import commands as supplier_commands


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the chosen command, and map input and credential errors to exit code 2."""
    # Top-level parser.
    parser = argparse.ArgumentParser(prog="sourcing", description="Source a BOM from approved suppliers, and find new suppliers.")
    # Subcommands from both abilities.
    commands = parser.add_subparsers(dest="command", required=True)
    cbom_commands.register(commands)
    supplier_commands.register(commands)
    learning_commands.register(commands, sourcing_estimator, ASSUMED)
    # Parse the command line.
    args = parser.parse_args(argv)
    # Warnings only from other libraries, on stderr.
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    # Progress lines from this package.
    logging.getLogger("sourcing_agent").setLevel(logging.INFO)
    try:
        # Run the command.
        return args.handler(args)
    except (InputError, OSError) as e:
        # Missing file, bad input, or a refused action.
        print(f"Input error: {e}", file=sys.stderr)
        return 2
    except openai.AuthenticationError:
        # Key present but rejected by OpenAI.
        print("OpenAI API key was rejected. Check OPENAI_API_KEY.", file=sys.stderr)
        # Match the existing credential-error exit code.
        return 2
    except anthropic.AuthenticationError:
        # Key present but rejected.
        print("Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2
    except TypeError as e:
        # No credentials found at all.
        if "authentication" not in str(e):
            raise
        print("No Anthropic credentials found. Set ANTHROPIC_API_KEY.", file=sys.stderr)
        return 2


# Allow python -m sourcing_agent.cli.
if __name__ == "__main__":
    sys.exit(main())
