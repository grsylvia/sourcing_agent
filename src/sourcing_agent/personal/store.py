"""Local personal preferences and command outcomes, separate from repository data."""

# Structured local storage.
import json
# Serialize simultaneous preference updates on WSL/Linux.
import fcntl
# Event timestamps.
from datetime import datetime, timezone
# Filesystem paths.
from pathlib import Path
# Shared local persistence.
from ..core.storage import write_json, append_jsonl
# Profile locations.
from ..core import paths
# Invalid personal configuration.
from ..core.errors import InputError

# Supported personal folder roles shared by setup and memory commands.
FOLDER_KINDS = ("templates", "boms", "cboms")


def load_memory() -> dict:
    """Read preferences without creating files or silently replacing broken data."""
    # A new user has no confirmed folder choices yet.
    if not paths.MEMORY_PATH.exists():
        # Missing choices trigger the skills' first-run question.
        return {"folders": {}, "lessons": []}
    # Decode saved preferences.
    try:
        # Read only this user's profile.
        memory = json.loads(paths.MEMORY_PATH.read_text(encoding="utf-8"))
    # Report corrupt memory for repair instead of overwriting it.
    except (ValueError, OSError) as exc:
        # Avoid exposing saved contents in error messages.
        raise InputError("Cannot read personal memory; repair memory.json before continuing.") from exc
    # Validate the small profile schema before using saved paths.
    if not isinstance(memory, dict) or not isinstance(memory.get("folders"), dict) or not isinstance(memory.get("lessons"), list):
        # Keep invalid preferences intact.
        raise InputError("Invalid personal memory schema.")
    # Validate folder values and saved lessons.
    if any(k not in FOLDER_KINDS or not isinstance(v, str) or not Path(v).expanduser().is_absolute() for k, v in memory["folders"].items()) or any(not isinstance(v, str) for v in memory["lessons"]):
        # Refuse ambiguous or malformed saved locations.
        raise InputError("Invalid personal memory folders or lessons.")
    # Return explicit preferences only.
    return memory


def folder(kind: str) -> Path:
    """Use a confirmed location or the current user's home as the CLI default."""
    # Expand saved home shortcuts for filesystem use.
    return Path(load_memory()["folders"].get(kind, str(Path.home()))).expanduser()


def bom_path(value: str) -> Path:
    """Resolve bare filenames in the personal BOM folder; preserve explicit paths."""
    # Expand quoted home shortcuts too.
    path = Path(value).expanduser()
    # A slash makes a relative path explicit, including ./file.csv.
    return folder("boms") / path if "/" not in value else path


def cbom_path(value: str) -> Path:
    """Resolve bare CBOM filenames in the personal output folder."""
    # Expand quoted home shortcuts too.
    path = Path(value).expanduser()
    # Explicit relative and absolute paths retain their usual meaning.
    return folder("cboms") / path if "/" not in value else path


def update_memory(folders: dict, note: str | None = None, sources_review: dict | None = None) -> dict:
    """Save chosen folders and a deduplicated user lesson without losing other choices."""
    # Restrict personal data to the current account by default.
    paths.USER_DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Lock across read-modify-write operations.
    with (paths.USER_DATA / "memory.lock").open("a") as lock:
        # Wait for any other preference update to finish.
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Preserve existing choices and lessons.
        memory = load_memory()
        # Normalize selected folders without moving existing files.
        memory["folders"].update({key: str(Path(value).expanduser().resolve()) for key, value in folders.items()})
        # Save only an explicitly acknowledged source revision.
        if sources_review is not None:
            # Retain the reviewed file identities and revision for startup checks.
            memory["sources_review"] = sources_review
        # Store only explicit, nonempty new lessons.
        if note and note.strip() and note.strip() not in memory["lessons"]:
            # Keep the original human-readable wording.
            memory["lessons"].append(note.strip())
        # Replace preferences only after the complete snapshot is ready.
        write_json(paths.MEMORY_PATH, memory)
        # Return saved preferences for display.
        return memory


def record_event(command: str, outcome: str) -> None:
    """Append only fixed command/outcome labels, never arguments or exception text."""
    # No paths, BOM contents, credentials or raw API messages enter command events.
    append_jsonl(paths.EVENTS_PATH, {"time": datetime.now(timezone.utc).isoformat(), "command": command, "outcome": outcome})
