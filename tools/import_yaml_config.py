"""Explicitly import the legacy YAML configuration into machine.db."""
from __future__ import annotations

import argparse
from pathlib import Path

from infrastructure.persistence import ConfigRepository, MachineDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import settings/cameras/rule_templates/rules YAML into machine.db"
    )
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--database", default="storage/machine.db")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument(
        "--reset", action="store_true",
        help="replace existing configuration; required for deliberate re-import",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    config_dir = resolve(project_root, args.config_dir).resolve()
    database_path = resolve(project_root, args.database).resolve()
    database = MachineDatabase(database_path)
    repository = ConfigRepository(database)
    revision = repository.import_yaml(config_dir, reset=args.reset)
    report = database.assert_valid()
    print(f"Imported YAML configuration into {database_path} (revision {revision}, "
          f"schema migrations {len(report['migrations'])}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
