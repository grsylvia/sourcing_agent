"""Small local persistence primitives: atomic snapshots and locked append-only records."""

# Complete JSON snapshots and one-line event records.
import json
# Atomic replacement on the same filesystem.
import os
# Concurrent local writers on Linux/WSL.
import fcntl
# Safely manage staged files.
from contextlib import contextmanager
# File locations.
from pathlib import Path
# Private staging files alongside their destination.
from tempfile import NamedTemporaryFile
# Stream template bytes into a staged file.
from shutil import copyfileobj


def copy_if_absent(source: Path, destination: Path) -> None:
    """Publish a complete file without replacing any existing destination."""
    # Existing user files require neither a source read nor a rewrite.
    if destination.is_file():
        # Preserve filled templates unchanged.
        return
    # Stage alongside the destination so publication stays on one filesystem.
    with NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        # Retain the staging path for cleanup on every exit.
        staged = Path(stream.name)
        # Incomplete copies must never appear at the destination.
        try:
            # Read the template only when it needs creating.
            with source.open("rb") as original:
                # A failed read or write leaves only a disposable staging file.
                copyfileobj(original, stream)
            # Flush the complete template before publishing it.
            stream.flush()
            # Close before linking the finished file into place.
            stream.close()
            # Publish without clobbering a file created concurrently.
            try:
                # Hard links create the destination exclusively and atomically.
                os.link(staged, destination)
            # A concurrent creator keeps ownership of its file.
            except FileExistsError:
                # Directories and dangling links are invalid template targets.
                if not destination.is_file():
                    # Surface an unusable destination instead of claiming success.
                    raise
        # Remove failed or successfully published staging names alike.
        finally:
            # A published hard link retains the complete bytes independently.
            staged.unlink(missing_ok=True)


@contextmanager
def atomic_text(path: Path):
    """Replace a text file only after its full contents are written successfully."""
    # Fresh personal storage defaults to owner-only access.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Stage on the destination filesystem so replacement is atomic.
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
        # Retain the name for cleanup if serialization fails.
        staged = Path(stream.name)
        # Every exit removes an unused staging file.
        try:
            # Let the caller serialize its format.
            yield stream
            # Flush before closing and replacing the destination.
            stream.flush()
            # Close before replacement for a complete snapshot.
            stream.close()
            # Existing data remains intact until this point.
            os.replace(staged, path)
        # Failed serialization never leaves a partial destination.
        finally:
            # Replacement already removed the staging pathname on success.
            staged.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    """Write one complete JSON snapshot without truncating an existing file."""
    # Share atomic replacement between preferences, caches, and registries.
    with atomic_text(path) as stream:
        # Preserve readable local records.
        json.dump(value, stream, indent=2)


def append_jsonl(path: Path, record: dict) -> None:
    """Append one complete JSON object under a local writer lock."""
    # Serialize before opening so invalid records cannot damage history.
    line = json.dumps(record) + "\n"
    # Create private storage when the first record arrives.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Open the append-only log without truncation.
    with path.open("a", encoding="utf-8") as stream:
        # Prevent interleaved records from simultaneous CLI processes.
        fcntl.flock(stream, fcntl.LOCK_EX)
        # Write one structured record without modifying earlier evidence.
        stream.write(line)
        # Flush while still holding the writer lock.
        stream.flush()


def read_jsonl(path: Path) -> list[dict]:
    """Read intact object records, tolerating damaged or non-object lines."""
    # A new user has no history.
    if not path.exists():
        # Missing history is different from a learned failure.
        return []
    # Accumulate valid records in append order.
    records = []
    # Stream input rather than reading another full copy of the file.
    with path.open(encoding="utf-8") as stream:
        # Independent records survive a damaged neighboring line.
        for line in stream:
            # Parse one complete line at a time.
            try:
                # Decode only JSON data, never executable instructions.
                value = json.loads(line)
            # An interrupted append may leave a damaged line.
            except ValueError:
                # Keep reading subsequent intact records.
                continue
            # Consumers expect mapping fields, never scalars or lists.
            if isinstance(value, dict):
                # Preserve backward-compatible record fields unchanged.
                records.append(value)
    # Return only usable object records.
    return records
