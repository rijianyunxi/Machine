"""Helpers for exposing stored snapshot files through the mounted web path."""

from pathlib import Path
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def snapshot_url(state, snapshot_path: str | None) -> str | None:
    """Convert a persisted filesystem path into a safe mounted snapshot URL."""
    if not snapshot_path:
        return None

    base = Path(state.snapshots_dir()).expanduser().resolve()
    path = Path(str(snapshot_path)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    try:
        relative = path.resolve().relative_to(base)
    except (OSError, ValueError):
        # Never expose an arbitrary filesystem path as a web URL.
        return None

    return "/snapshots/" + quote(relative.as_posix(), safe="/")
