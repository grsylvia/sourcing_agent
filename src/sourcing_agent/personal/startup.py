"""Offline startup service: source snapshots, folder initialization, and status."""

# Encode a stable source revision.
import json
# Detect changes to the reviewed contents.
from hashlib import sha256
# Record explicit user acknowledgment time.
from datetime import datetime, timezone
# Resolve selected source and folder paths.
from pathlib import Path
# Shared repository and personal locations.
from ..core import paths
# Validate the exact supplier text included in the review.
from ..core.config import parse_suppliers
# Report stale acknowledgments without saving them.
from ..core.errors import InputError
# Publish complete templates without overwriting user work.
from ..core.storage import copy_if_absent
# Preserve existing personal choices and shared folder roles.
from .store import FOLDER_KINDS, load_memory, update_memory


def source_review(suppliers: Path) -> dict:
    """Read each source once so display and acknowledgment use the same snapshot."""
    # File locations are provenance, independent of their content revision.
    supplier_file, discovery_file = suppliers.resolve(), paths.SUPPLIER_SOURCES_PATH.resolve()
    # Read one exact supplier snapshot.
    supplier_text = supplier_file.read_text(encoding="utf-8")
    # Read one exact discovery-guide snapshot.
    discovery_text = discovery_file.read_text(encoding="utf-8")
    # Validate precisely the supplier content being acknowledged.
    config = parse_suppliers(supplier_text, supplier_file)
    # Moving identical sources between checkouts does not change their revision.
    revision = sha256(json.dumps([supplier_text, discovery_text]).encode()).hexdigest()
    # Return readable evidence and its stable content identity.
    return {
        # Acknowledgment binds both source snapshots together.
        "revision": revision,
        # Keep selected file locations visible to the reviewer.
        "supplier_file": str(supplier_file),
        # The discovery guide is separate from searchable supplier domains.
        "discovery_file": str(discovery_file),
        # Quote currency comes from the selected configuration.
        "currency": config["currency"],
        # Show the categories available for BOM input.
        "categories": config["categories"],
        # Include names, domains, and category coverage.
        "suppliers": config["suppliers"],
        # Linked sources are displayed without fetching them.
        "discovery_sources": discovery_text,
    }


def initialize_folders(folders: dict) -> None:
    """Create selected folders and preserve any existing filled template."""
    # Older valid profiles may contain home-relative paths.
    destinations = {key: Path(value).expanduser() for key, value in folders.items()}
    # Create chosen folders before copying the template.
    for destination in destinations.values():
        # Parent creation supports a fresh home subfolder.
        destination.mkdir(parents=True, exist_ok=True)
    # A partial setup may not yet include the template choice.
    if "templates" in destinations:
        # Failed copies leave no partial workbook to confuse later startup.
        copy_if_absent(paths.PROJECT_ROOT / "templates" / "bom_template.xlsx", destinations["templates"] / "bom_template.xlsx")


def startup_status(memory: dict, review: dict) -> dict:
    """Describe outstanding startup steps without changing personal state."""
    # Missing directories need initialization even when their choices are saved.
    missing = [key for key in FOLDER_KINDS if key not in memory["folders"] or not Path(memory["folders"][key]).expanduser().is_dir()]
    # A deleted or never-copied template is also an unfinished startup step.
    template_folder = memory["folders"].get("templates")
    # Require a usable workbook path without inspecting user-filled contents.
    template_required = template_folder is None or not (Path(template_folder).expanduser() / "bom_template.xlsx").is_file()
    # Older profiles retain their choices but still need source review.
    reviewed = memory.get("sources_review", {})
    # Invalid optional metadata cannot imply acceptance.
    review_required = not isinstance(reviewed, dict) or reviewed.get("revision") != review["revision"]
    # Keep the CLI report separate from the setup operations.
    return {
        # Display the active private profile.
        "location": str(paths.USER_DATA),
        # Preserve all previously selected destinations.
        "folders": memory["folders"],
        # Identify folder choices or directories needing initialization.
        "missing_folders": missing,
        # Allow the skill to repair a missing template using the saved folder.
        "template_required": template_required,
        # Source acceptance is distinct from folder setup.
        "sources_review_required": review_required,
        # Completion requires folders, template, and the current review.
        "startup_required": bool(missing) or template_required or review_required,
        # Provide the exact source snapshot to show before acknowledgment.
        "sources": review,
    }


def startup(suppliers: Path, selected: dict, accepted_revision: str | None = None) -> dict:
    """Inspect startup or apply explicit folder choices and source acknowledgment."""
    # A read-only invocation must not create a profile.
    memory = load_memory()
    # Display and validate one coherent source snapshot.
    review = source_review(suppliers)
    # Reject stale acknowledgment before making filesystem changes.
    if accepted_revision is not None and accepted_revision != review["revision"]:
        # The user must see the changed sources first.
        raise InputError("Sources changed; review sourcing startup and accept the new revision.")
    # Inspection has no filesystem side effects.
    if selected or accepted_revision is not None:
        # Normalize new choices while retaining existing folder preferences.
        folders = memory["folders"] | {key: str(Path(value).expanduser().resolve()) for key, value in selected.items()}
        # Create complete user-facing files before recording successful setup.
        initialize_folders(folders)
        # No source review is inferred from folder initialization.
        acknowledgment = None
        # Only an explicitly supplied current revision records acceptance.
        if accepted_revision is not None:
            # Keep provenance and timestamp out of free-form personal notes.
            acknowledgment = {key: review[key] for key in ("revision", "supplier_file", "discovery_file")}
            # Timestamp the user's explicit review decision.
            acknowledgment["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        # Update only selected preferences under the profile writer lock.
        memory = update_memory(selected, sources_review=acknowledgment)
    # Return facts for the CLI to render.
    return startup_status(memory, review)
