"""Historical audit record for baseline ``33e95b2``.

This file used to execute probes that asserted known-bad behavior.  The four
probes implemented in the first remediation bundle were migrated to normal
regression tests, so this record deliberately does not re-run their obsolete
assertions against corrected code.

Regression coverage now lives in:
  - tests/test_train_registration_security.py
  - tests/test_inference_isolation.py
  - tests/test_dataset_service.py
  - tests/test_temporal_cooldown.py

The remaining snapshot/auth findings are intentionally still open and are
tracked in ``docs/分阶段整改计划.md``; they are not encoded as passing product
expectations here.
"""

from __future__ import annotations

import json


HISTORICAL_FINDINGS = {
    "baseline_commit": "33e95b2",
    "fixed_in_first_remediation_bundle": {
        "threshold_override": "tests/test_inference_isolation.py",
        "training_paths": "tests/test_train_registration_security.py",
        "duration_cooldown": "tests/test_temporal_cooldown.py",
        "prelabel_mapping": "tests/test_dataset_service.py",
    },
    "still_open": ["snapshot_mount", "auth_lifecycle"],
}


if __name__ == "__main__":
    print(json.dumps(HISTORICAL_FINDINGS, ensure_ascii=False, indent=2))
