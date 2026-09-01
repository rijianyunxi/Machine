"""Immutable-ish runtime configuration snapshot."""

from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigSnapshot:
    """A coherent configuration view consumed by runtime workers.

    The nested values are copied on creation and must be treated as read-only
    by consumers. A fresh snapshot is atomically published after each commit.
    """

    revision: int
    settings: dict
    cameras: tuple[dict, ...]
    rules: tuple[object, ...]
    templates: dict

    @classmethod
    def build(
        cls, revision: int, settings: dict, cameras: list[dict],
        rules: list | None = None, templates: dict | None = None,
    ):
        return cls(
            revision=int(revision),
            settings=copy.deepcopy(settings),
            cameras=tuple(copy.deepcopy(c) for c in cameras),
            rules=tuple(copy.deepcopy(r) for r in (rules or [])),
            templates=copy.deepcopy(templates or {}),
        )
