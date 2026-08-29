"""
Rules engine - template-driven, persisted to config/rules.yaml.

Rules are data, not code (manageable from the web panel):
  - Each rule instance picks a TEMPLATE (ppe_absence / presence_near_person)
    and carries its own params, bound models and severity.
  - On first run the built-in seed rules (1/13/14) are migrated to
    config/rules.yaml with their original IDs so alert history stays valid.
  - The store watches the YAML mtime, so edits (panel or manual) are picked
    up on the next frame without restart.
"""

import copy
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

# ============================================================
# Rule templates
# ============================================================
# Each template defines which params a rule instance carries. The panel uses
# PARAM_SPECS to render edit forms dynamically; the analyzer dispatches on
# template name.

TEMPLATE_PPE_ABSENCE = "ppe_absence"
TEMPLATE_PRESENCE_NEAR_PERSON = "presence_near_person"

# Param specs: ``from_model`` marks params whose classes must exist in the
# rule's bound models (used for panel validation and model routing). Params
# like person_classes are typically supplied by *another* model (e.g. a PPE
# model detecting Person), so they are resolved dynamically instead.
PARAM_SPECS = {
    TEMPLATE_PPE_ABSENCE: {
        "label": "装备缺失检查（如未戴安全帽）",
        "params": [
            {"name": "person_classes", "type": "classes", "default": ["person"],
             "desc": "人员类别（可来自其他模型，如 PPE 模型的 Person）",
             "from_model": False},
            {"name": "required_classes", "type": "classes", "default": ["hardhat"],
             "desc": "必须佩戴的装备类别（框与人员重叠视为已佩戴）",
             "from_model": True},
            {"name": "absence_classes", "type": "classes", "default": [],
             "desc": "一票否决类别（检出即违规，如 no-hardhat）",
             "from_model": True},
            {"name": "coverage_ratio", "type": "float", "default": 0.5, "min": 0.0,
             "max": 1.0, "desc": "装备框被人员框覆盖的比例阈值", "from_model": False},
        ],
    },
    TEMPLATE_PRESENCE_NEAR_PERSON: {
        "label": "目标出现在人员附近（如吸烟/持烟）",
        "params": [
            {"name": "trigger_classes", "type": "classes", "default": ["cigarette"],
             "desc": "触发类别（检出且靠近人员即违规）", "from_model": True},
            {"name": "person_classes", "type": "classes", "default": ["person"],
             "desc": "人员类别（可来自其他模型，如 PPE 模型的 Person）",
             "from_model": False},
            {"name": "overlap_margin", "type": "float", "default": 0.2, "min": 0.0,
             "max": 2.0, "desc": "触发框相对人员框的外扩比例", "from_model": False},
            {"name": "min_confidence", "type": "float", "default": 0.0, "min": 0.0,
             "max": 1.0, "desc": "触发检测的最低置信度（0 为不限制）",
             "from_model": False},
        ],
    },
}


@dataclass
class RuleDefinition:
    """A rule instance: template + params + binding."""

    id: int
    name: str
    description: str
    category: str = "ppe"                       # kept for log display
    template: str = TEMPLATE_PRESENCE_NEAR_PERSON
    models: list = field(default_factory=list)  # bound model names (empty = all)
    params: dict = field(default_factory=dict)
    severity: int = 2
    enabled: bool = True


# Seed rules migrated to rules.yaml on first run (IDs preserved for DB history).
_SEED_RULES = [
    {
        "id": 1,
        "name": "no_safety_helmet",
        "description": "Worker without safety helmet",
        "template": TEMPLATE_PPE_ABSENCE,
        "models": ["ppe"],
        "severity": 3,
        "enabled": True,
        "params": {
            "person_classes": ["person"],
            "required_classes": ["hardhat"],
            "absence_classes": ["no-hardhat"],
            "coverage_ratio": 0.5,
        },
    },
    {
        "id": 13,
        "name": "smoking_no_fire_zone",
        "description": "Smoking detected in no-fire zone",
        "template": TEMPLATE_PRESENCE_NEAR_PERSON,
        "models": ["smoking"],
        "severity": 4,
        "enabled": True,
        "params": {
            "trigger_classes": ["cigarette", "smoking"],
            "person_classes": ["person"],
            "overlap_margin": 0.2,
            "min_confidence": 0.0,
        },
    },
    {
        "id": 14,
        "name": "person_holding_cigarette",
        "description": "Person holding/with cigarette in no-fire zone",
        "template": TEMPLATE_PRESENCE_NEAR_PERSON,
        "models": ["smoking"],
        "severity": 3,
        "enabled": True,
        "params": {
            "trigger_classes": ["cigarette"],
            "person_classes": ["person"],
            "overlap_margin": 0.1,
            "min_confidence": 0.0,
        },
    },
]


class RulesStore:
    """rules.yaml loader with mtime-based hot reload and CRUD."""

    def __init__(self, config_dir="config"):
        self._path = Path(config_dir) / "rules.yaml"
        self._lock = threading.Lock()
        self._cache: list[RuleDefinition] = []
        self._mtime: float = -1.0
        self._yaml = YAML(typ="rt")  # round-trip: preserve comments
        self._seed_if_missing()

    # ---------- persistence ----------

    def _seed_if_missing(self):
        if self._path.exists():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = CommentedMap()
        data["rules"] = self._to_yaml_list(_SEED_RULES)
        self._yaml.dump(data, self._path.open("w", encoding="utf-8"))

    @staticmethod
    def _to_yaml_list(entries):
        out = []
        for e in entries:
            m = CommentedMap()
            for k, v in e.items():
                m[k] = copy.deepcopy(v)
            out.append(m)
        return out

    def _load(self):
        raw = self._yaml.load(self._path.open("r", encoding="utf-8")) or {}
        rules = []
        for r in raw.get("rules", []):
            template = r.get("template", TEMPLATE_PRESENCE_NEAR_PERSON)
            if template not in PARAM_SPECS:
                continue  # unknown template: skip rather than crash detection
            rules.append(
                RuleDefinition(
                    id=int(r["id"]),
                    name=r.get("name", f"rule_{r['id']}"),
                    description=r.get("description", ""),
                    category=r.get("category", "ppe"),
                    template=template,
                    models=list(r.get("models", []) or []),
                    params=dict(r.get("params", {}) or {}),
                    severity=int(r.get("severity", 2)),
                    enabled=bool(r.get("enabled", True)),
                )
            )
        return rules

    def _save(self, rules: list[RuleDefinition]):
        data = CommentedMap()
        data["rules"] = self._to_yaml_list(
            [
                {
                    "id": r.id,
                    "name": r.name,
                    "description": r.description,
                    "template": r.template,
                    "models": list(r.models),
                    "params": dict(r.params),
                    "severity": r.severity,
                    "enabled": r.enabled,
                }
                for r in rules
            ]
        )
        with self._lock:
            self._yaml.dump(data, self._path.open("w", encoding="utf-8"))
            self._mtime = -1.0  # force reload

    # ---------- reads (mtime-aware) ----------

    def get_all(self) -> list[RuleDefinition]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            mtime = -1.0
        if mtime != self._mtime:
            with self._lock:
                if mtime != self._mtime:
                    try:
                        self._cache = self._load()
                        self._mtime = mtime
                    except Exception:
                        pass  # keep last good cache on broken YAML
        return list(self._cache)

    def get_rules_for_camera(self, rule_ids) -> list[RuleDefinition]:
        """Enabled rule definitions matching the camera's active rule IDs."""
        ids = set(rule_ids or [])
        return [r for r in self.get_all() if r.enabled and r.id in ids]

    def get_by_id(self, rule_id: int):
        for r in self.get_all():
            if r.id == rule_id:
                return r
        return None

    def next_free_id(self) -> int:
        used = {r.id for r in self.get_all()}
        nid = 1
        while nid in used:
            nid += 1
        return nid

    # ---------- writes ----------

    def add(self, rule: RuleDefinition) -> RuleDefinition:
        rules = self.get_all()
        if any(r.id == rule.id for r in rules):
            raise ValueError(f"规则 ID {rule.id} 已存在")
        rules.append(rule)
        rules.sort(key=lambda r: r.id)
        self._save(rules)
        return rule

    def update(self, rule_id: int, fields: dict) -> RuleDefinition:
        rules = self.get_all()
        target = None
        for r in rules:
            if r.id == rule_id:
                target = r
                break
        if target is None:
            raise ValueError(f"规则 {rule_id} 不存在")
        for key in ("name", "description", "template", "models", "params",
                    "severity", "enabled"):
            if key in fields:
                setattr(target, key, fields[key])
        if "template" in fields and fields["template"] not in PARAM_SPECS:
            raise ValueError(f"未知模板类型: {fields['template']}")
        self._save(rules)
        return target

    def delete(self, rule_id: int):
        rules = [r for r in self.get_all() if r.id != rule_id]
        if len(rules) == len(self.get_all()):
            raise ValueError(f"规则 {rule_id} 不存在")
        self._save(rules)


# Module-level singleton so main / analyzer / panel share one cache.
_store: RulesStore | None = None


def get_rules_store(config_dir="config") -> RulesStore:
    global _store
    if _store is None:
        _store = RulesStore(config_dir)
    return _store


# Backward-compatible helpers ----------------------------------------------

def get_rules_for_camera(active_rule_ids, config_dir="config") -> list[RuleDefinition]:
    return get_rules_store(config_dir).get_rules_for_camera(active_rule_ids)


def get_all_rules(config_dir="config") -> list[RuleDefinition]:
    return sorted(get_rules_store(config_dir).get_all(), key=lambda r: r.id)


def get_template_specs() -> dict:
    """Template metadata for the panel's dynamic rule edit forms."""
    return {
        name: {"label": spec["label"], "params": spec["params"]}
        for name, spec in PARAM_SPECS.items()
    }
