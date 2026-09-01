"""Restore a machine backup produced by backup_machine.py."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from infrastructure.persistence import MachineDatabase

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a machine backup")
    parser.add_argument("backup_dir")
    parser.add_argument("--database", default="storage/machine.db")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--restore-files", action="store_true",
                        help="also restore backed-up models and snapshots")
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
            relative = Path(entry.get("relative_path", ""))
            if not relative or relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"非法备份相对路径: {relative}")
            source = files_dir / relative
            if not source.is_file():
                continue
            destination = (project_root / "storage" / "snapshots" / Path(*relative.parts[1:])
                          if relative.parts and relative.parts[0] == "snapshots"
                          else project_root / relative)
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
