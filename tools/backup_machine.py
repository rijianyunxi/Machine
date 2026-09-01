"""Create a consistent machine.db + external file backup."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

from infrastructure.persistence import ConfigRepository, MachineDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def copy_tree(source: Path, destination: Path) -> list[dict]:
    entries = []
    if not source.is_dir():
        return entries
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        entries.append({
            "source": str(item),
            "backup_path": str(target),
            "relative_path": str(Path("snapshots") / relative),
            "sha256": sha256(item),
            "size": item.stat().st_size,
        })
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup machine.db and external runtime files")
    parser.add_argument("--database", default="storage/machine.db")
    parser.add_argument("--output", default="storage/backups")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    database_path = resolve(project_root, args.database).resolve()
    output_root = resolve(project_root, args.output).resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = output_root / f"machine-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    database = MachineDatabase(database_path)
    repository = ConfigRepository(database)
    db_backup = database.backup_to(backup_dir / "machine.db")
    db_report = MachineDatabase(db_backup).assert_valid()

    public_config = repository.export_public_config()
    public_config_path = backup_dir / "config-public.json"
    public_config_path.write_text(
        json.dumps(public_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    files_dir = backup_dir / "files"
    files = []
    for model in repository.get_models():
        model_path = resolve(project_root, model.get("path", ""))
        if model_path.is_file():
            relative = Path("models") / model_path.name
            target = files_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(model_path, target)
            files.append({"kind": "model", "source": str(model_path),
                          "backup_path": str(target), "relative_path": str(relative),
                          "sha256": sha256(model_path), "size": model_path.stat().st_size})
        else:
            files.append({"kind": "model", "source": str(model_path),
                          "relative_path": str(Path("models") / model_path.name),
                          "missing": True})

    snapshot_dir = resolve(project_root, repository.get_settings().get("snapshot", {}).get("save_dir", "storage/snapshots"))
    snapshot_target = files_dir / "snapshots"
    files.extend({"kind": "snapshot", **entry} for entry in copy_tree(snapshot_dir, snapshot_target))

    manifest = {
        "format": "machine-backup",
        "format_version": 1,
        "created_at": int(time.time()),
        "database": str(db_backup),
        "schema": db_report,
        "public_config": str(public_config_path),
        "files": files,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(str(backup_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
