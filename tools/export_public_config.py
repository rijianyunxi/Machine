"""Export a redacted, non-sensitive configuration snapshot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from infrastructure.persistence import ConfigRepository, MachineDatabase

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export machine.db configuration without secrets or URI passwords"
    )
    parser.add_argument("--database", default="storage/machine.db")
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    database_path = resolve(project_root, args.database).resolve()
    output_path = resolve(project_root, args.output).resolve()
    repository = ConfigRepository(MachineDatabase(database_path))
    repository.database.assert_valid()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(repository.export_public_config(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported redacted configuration to {output_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
