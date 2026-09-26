"""Offline CLI for personal preferences and past command outcomes."""

# Terminal JSON format.
import json
# Summarize safe outcome labels.
from collections import Counter
# Folder arguments.
from pathlib import Path
# Shared tolerant event reader.
from ..core.storage import read_jsonl
# Personal data locations.
from ..core import paths
# Preference persistence.
from .store import FOLDER_KINDS, load_memory, update_memory
# Offline onboarding and source review.
from .startup import startup


def register(commands) -> None:
    """Expose personal preferences without making network calls."""
    # Inspect or update the current user's memory.
    parser = commands.add_parser("memory", help="Show or update local personal folders and lessons.")
    # Route to the offline handler.
    parser.set_defaults(handler=cmd_memory)
    # Allow independent destinations for each file role.
    for kind in FOLDER_KINDS:
        # Omitted choices preserve saved preferences.
        parser.add_argument(f"--{kind}", type=Path, help=f"Save the {kind} folder for this user.")
    # Store an explicit personal lesson for future skill runs.
    parser.add_argument("--note", help="Remember an explicit user-specific preference or local fact; shared rules belong in skills/code, never here.")
    # Expose the offline startup sequence alongside memory inspection.
    setup = commands.add_parser("startup", help="Initialize personal folders and review default sources (offline).")
    # Route setup through its service without mixing rendering into it.
    setup.set_defaults(handler=cmd_startup)
    # Folder choices stay independently configurable.
    for kind in FOLDER_KINDS:
        # Omitted options preserve previously saved choices.
        setup.add_argument(f"--{kind}", type=Path)
    # Review the supplier list selected for the workflow.
    setup.add_argument("--suppliers", type=Path, default=paths.DEFAULT_SUPPLIERS)
    # Record only the source revision explicitly accepted by the user.
    setup.add_argument("--accept-sources", metavar="REVISION", help="Record the displayed revision only after the user accepts it.")


def selected_folders(args) -> dict:
    """Extract explicit folder choices shared by memory and startup commands."""
    # Omitted destinations must never reset existing choices.
    return {key: getattr(args, key) for key in FOLDER_KINDS if getattr(args, key) is not None}


def cmd_startup(args) -> int:
    """Render the offline startup service result."""
    # Setup applies only the choices explicitly supplied to the command.
    report = startup(args.suppliers, selected_folders(args), args.accept_sources)
    # Keep terminal JSON out of the service layer.
    print(json.dumps(report, indent=2))
    # This workflow never calls an API.
    return 0


def cmd_memory(args) -> int:
    """Display memory and whether folder setup is still needed."""
    # Collect only user-supplied folder choices.
    selected = selected_folders(args)
    # A read-only invocation never creates a profile.
    memory = update_memory(selected, args.note) if selected or args.note else load_memory()
    # Count past outcomes for the skill's next-run review.
    outcomes = Counter()
    # New users have no events; damaged records do not erase intact history.
    for event in read_jsonl(paths.EVENTS_PATH):
        # Count only structured command outcomes.
        if isinstance(event.get("command"), str) and isinstance(event.get("outcome"), str):
            # Summarize commands and outcomes together.
            outcomes[f"{event['command']}: {event['outcome']}"] += 1
    # Include first-run state and the storage location for the skills.
    print(json.dumps({"location": str(paths.USER_DATA), "setup_required": any(key not in memory["folders"] for key in FOLDER_KINDS), **memory, "command_outcomes": dict(outcomes)}, indent=2))
    # Preferences are available without an API call.
    return 0
