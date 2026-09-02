"""Canonical paths for runtime-managed storage.

The application keeps mutable runtime data below ``storage/``.  Model and
 dataset paths are persisted in the database, so this module also provides
 backwards-compatible resolution for entries written before the move from the
 project-root ``models/`` and ``datasets/`` directories.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_DIR = PROJECT_ROOT / "storage"
MODELS_DIR = STORAGE_DIR / "models"
DATASETS_DIR = STORAGE_DIR / "datasets"
LEGACY_MODELS_DIR = PROJECT_ROOT / "models"
LEGACY_DATASETS_DIR = PROJECT_ROOT / "datasets"


def ensure_storage_dirs() -> None:
    """Create the storage directories used by the application."""
    for directory in (STORAGE_DIR, MODELS_DIR, DATASETS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _move_children(source: Path, destination: Path) -> int:
    """Move legacy children when possible, never overwriting new data."""
    if not source.is_dir() or source.resolve() == destination.resolve():
        return 0
    moved = 0
    destination.mkdir(parents=True, exist_ok=True)
    for child in list(source.iterdir()):
        target = destination / child.name
        if target.exists():
            # Keep the legacy copy available for the resolver below.  This is
            # safer than silently replacing user data during startup.
            continue
        shutil.move(str(child), str(target))
        moved += 1
    return moved


def migrate_legacy_runtime_dirs() -> dict[str, int]:
    """Move root-level models/datasets into storage without data loss.

    Existing database rows may still contain ``models/foo.pt`` or
    ``datasets/name``.  The resolvers intentionally continue to understand
    those references, including the rare case where a same-named target had
    to be left in the legacy directory.
    """
    ensure_storage_dirs()
    return {
        "models": _move_children(LEGACY_MODELS_DIR, MODELS_DIR),
        "datasets": _move_children(LEGACY_DATASETS_DIR, DATASETS_DIR),
    }


def _is_windows_absolute(value: str) -> bool:
    # On non-Windows hosts Path does not recognize a Windows drive path.  It
    # is still useful to handle such persisted values consistently in tools.
    return len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")


def _as_path(value: str | os.PathLike[str] | Path) -> Path:
    return Path(os.fspath(value))


def _model_candidates(value: str | os.PathLike[str] | Path) -> list[Path]:
    raw = os.fspath(value)
    path = _as_path(raw)
    if path.is_absolute() or _is_windows_absolute(raw):
        return [path]

    normalized = raw.replace("\\", "/").lstrip("./")
    candidates: list[Path] = []
    if normalized == "models" or normalized.startswith("models/"):
        relative = normalized[len("models"):].lstrip("/")
        candidates.append(MODELS_DIR / relative)
        candidates.append(LEGACY_MODELS_DIR / relative)
    elif normalized == "storage/models" or normalized.startswith("storage/models/"):
        relative = normalized[len("storage/models"):].lstrip("/")
        candidates.append(MODELS_DIR / relative)
        candidates.append(LEGACY_MODELS_DIR / relative)
    else:
        # A bare filename is the form used by the upload/training APIs.
        candidates.extend((MODELS_DIR / normalized, LEGACY_MODELS_DIR / normalized))
        candidates.append(PROJECT_ROOT / normalized)
    candidates.append(path)
    return list(dict.fromkeys(candidates))


def resolve_model_path(value: str | os.PathLike[str] | Path) -> Path:
    """Resolve a model reference, preferring storage/models.

    The returned path is absolute when the reference is relative.  If no
    candidate exists, the canonical storage path is returned for a relative
    model reference so callers can report a useful location.
    """
    candidates = _model_candidates(value)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    first = candidates[0]
    return first if first.is_absolute() else (PROJECT_ROOT / first).resolve()


def canonical_model_reference(value: str | os.PathLike[str] | Path) -> str:
    """Return the stable database/UI reference ``storage/models/<file>``."""
    raw = os.fspath(value).replace("\\", "/")
    path = resolve_model_path(value)
    for root in (MODELS_DIR, LEGACY_MODELS_DIR):
        try:
            relative = path.resolve().relative_to(root.resolve())
            return (Path("storage") / "models" / relative).as_posix()
        except ValueError:
            continue
    if not Path(raw).is_absolute() and not _is_windows_absolute(raw):
        return (Path("storage") / "models" / Path(raw).name).as_posix()
    return raw


def resolve_dataset_dir(name: str) -> Path:
    """Resolve a dataset name, preferring storage/datasets and supporting old data."""
    target = DATASETS_DIR / name
    if target.exists():
        return target
    legacy = LEGACY_DATASETS_DIR / name
    return legacy if legacy.exists() else target


def canonical_dataset_reference(name: str) -> str:
    return (Path("storage") / "datasets" / name).as_posix()
