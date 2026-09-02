"""Restore a machine backup produced by backup_machine.py."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from infrastructure.persistence import MachineDatabase
from infrastructure.storage_paths import DATASETS_DIR, MODELS_DIR, STORAGE_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def restore_destination(project_root: Path, relative: Path) -> Path:
    """Map backup-relative paths into storage, including legacy backups."""
    parts = relative.parts
    if not parts:
        raise ValueError("空的备份相对路径")
    roots = {
        "models": project_root / "storage" / "models",
        "datasets": project_root / "storage" / "datasets",
        "snapshots": project_root / "storage" / "snapshots",
    }
    if parts[0] in roots:
        return roots[parts[0]].joinpath(*parts[1:])
    # Older backups could contain a project-relative path.  Only allow the
    # known runtime folders and still place them under storage.
    raise ValueError(f"不支持的备份路径: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a machine backup")
    parser.add_argument("backup_dir")
    parser.add_argument("--database", default="storage/machine.db")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--restore-files", action="store_true",
                        help="also restore backed-up models, datasets and snapshots")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    backup_dir = Path(args.backup_dir).resolve()
    manifest_path = backup_dir / "manifest.json"
    database_backup = backup_dir / "machine.db"
    if not manifest_path.is_file() or not database_backup.is_file():
        raise FileNotFoundError("备份目录缺少 manifest.json 或 machine.db")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "machine-backup":
        raise ValueError("不是 machine-backup 格式")

    target_database = resolve(project_root, args.database).resolve()
    restored = MachineDatabase.restore_from(database_backup, target_database)
    report = MachineDatabase(restored).assert_valid()

    restored_files = 0
    if args.restore_files:
        files_dir = backup_dir / "files"
        for entry in manifest.get("files", []):
            relative = Path(str(entry.get("relative_path", "")))
            if (not relative or relative.is_absolute() or ".." in relative.parts
                    or relative.parts[0] not in {"models", "datasets", "snapshots"}):
                raise ValueError(f"非法备份相对路径: {relative}")
            source = files_dir / relative
            if not source.is_file():
                continue
            destination = restore_destination(project_root, relative)
            if not inside(project_root, destination):
                raise ValueError(f"拒绝恢复到项目目录之外: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            restored_files += 1

    print(json.dumps({"database": str(restored), "schema": report,
                      "restored_files": restored_files}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
