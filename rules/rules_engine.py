"""
Rules engine - template-driven, persisted to config/rules.yaml.

Rules are data, not code (manageable from the web panel):
  - Each rule instance picks a TEMPLATE (defined in config/rule_templates.yaml)
    and carries its own params, bound models and severity.
  - A template binds a label + param schema to one check LOGIC primitive
    (CHECK_LOGICS, implemented in core/analyzer.py). Templates are config, so
    adding a template type = a YAML entry / a few clicks in the panel; truly
    new detection behaviour = a new primitive (code).
  - On first run the built-in seed rules (1/13/14) are migrated to
    config/rules.yaml and the seed templates to config/rule_templates.yaml.
  - Both stores watch their YAML mtime, so edits (panel or manual) are picked
    up on the next frame without restart.
"""

import copy
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

TEMPLATE_PPE_ABSENCE = "ppe_absence"
TEMPLATE_PRESENCE_NEAR_PERSON = "presence_near_person"

# ============================================================
# Check logic primitives (implemented in core/analyzer.py)
# ============================================================
# The analyzer dispatches on the template's ``logic`` field, not its name.

LOGIC_PRESENCE = "presence"
LOGIC_PRESENCE_NEAR = "presence_near"
LOGIC_ABSENCE_REQUIRED = "absence_required"

CHECK_LOGICS = {
    LOGIC_PRESENCE: "检出即违规：画面出现触发类别就告警（如明火、打电话）",
    LOGIC_PRESENCE_NEAR: "靠近人员才违规：触发类检出且与人员框重叠才告警（如吸烟）",
    LOGIC_ABSENCE_REQUIRED: "装备缺失即违规：人员未被必需装备框覆盖，或检出违规类（如未戴安全帽）",
}

# Seed templates migrated to rule_templates.yaml on first run.
# ``from_model`` marks params whose classes must exist in the rule's bound
# models (used for panel validation). Params like person_classes are typically
# supplied by *another* model (e.g. a PPE model detecting Person), so they are
# resolved dynamically instead.
_SEED_TEMPLATES = {
    TEMPLATE_PPE_ABSENCE: {
        "label": "装备缺失检查（如未戴安全帽）",
        "logic": LOGIC_ABSENCE_REQUIRED,
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
        "logic": LOGIC_PRESENCE_NEAR,
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

_PARAM_TYPES = ("classes", "list", "float", "int")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TemplateStore:
    """rule_templates.yaml loader with mtime-based hot reload and CRUD."""

    def __init__(self, config_dir="config"):
        self._path = Path(config_dir) / "rule_templates.yaml"
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._mtime: float = -1.0
        self._yaml = YAML(typ="rt")  # round-trip: preserve comments
        self._seed_if_missing()

    # ---------- persistence ----------

    def _seed_if_missing(self):
        if self._path.exists():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = CommentedMap()
        data["templates"] = self._to_yaml_dict(_SEED_TEMPLATES)
        self._yaml.dump(data, self._path.open("w", encoding="utf-8"))

    @staticmethod
    def _to_yaml_dict(templates: dict) -> CommentedMap:
        out = CommentedMap()
        for name, spec in templates.items():
            m = CommentedMap()
            m["label"] = spec["label"]
            m["logic"] = spec["logic"]
            params = []
            for p in spec.get("params", []):
                pm = CommentedMap()
                for k, v in p.items():
                    pm[k] = copy.deepcopy(v)
                params.append(pm)
            m["params"] = params
            out[name] = m
        return out

    def _load(self) -> dict:
        raw = self._yaml.load(self._path.open("r", encoding="utf-8")) or {}
        templates = {}
        for name, spec in (raw.get("templates") or {}).items():
            spec = spec or {}
            templates[str(name)] = {
                "label": str(spec.get("label") or name),
                "logic": str(spec.get("logic") or ""),
                "params": [dict(p) for p in (spec.get("params") or [])],
            }
        return templates

    def _save(self, templates: dict):
        with self._lock:
            self._yaml.dump({"templates": self._to_yaml_dict(templates)},
                            self._path.open("w", encoding="utf-8"))
            self._mtime = -1.0  # force reload

    # ---------- reads (mtime-aware) ----------

    def get_all(self) -> dict:
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
        return dict(self._cache)

    def logic_of(self, template: str) -> str | None:
        spec = self.get_all().get(template)
        return spec["logic"] if spec else None

    def specs(self) -> dict:
        """Template metadata for the panel's dynamic rule edit forms."""
        return self.get_all()

    # ---------- validation ----------

    @staticmethod
    def validate(name: str, spec: dict) -> dict:
        """Normalize + validate one template definition; raises ValueError."""
        if not _NAME_RE.match(name or ""):
            raise ValueError("模板名只能用小写字母/数字/下划线，且以字母开头")
        label = str(spec.get("label") or "").strip()
        if not label:
            raise ValueError("模板显示名称不能为空")
        logic = str(spec.get("logic") or "").strip()
        if logic not in CHECK_LOGICS:
            raise ValueError(
                f"未知检测原语: {logic}，可选: {', '.join(CHECK_LOGICS)}")
        params, seen = [], set()
        for p in spec.get("params") or []:
            pname = str(p.get("name") or "").strip()
            if not _NAME_RE.match(pname):
                raise ValueError(f"参数名不合法: {pname!r}")
            if pname in seen:
                raise ValueError(f"参数名重复: {pname}")
            seen.add(pname)
            ptype = str(p.get("type") or "classes")
            if ptype not in _PARAM_TYPES:
                raise ValueError(f"参数 {pname} 类型不支持: {ptype}")
            clean = {"name": pname, "type": ptype,
                     "desc": str(p.get("desc") or pname)}
            default = p.get("default")
            if ptype in ("float", "int"):
                try:
                    default = float(default) if ptype == "float" \
                        else int(float(default))
                except (TypeError, ValueError):
                    raise ValueError(f"参数 {pname} 默认值必须是数字")
                if p.get("min") is not None:
                    clean["min"] = float(p["min"])
                if p.get("max") is not None:
                    clean["max"] = float(p["max"])
            else:
                if isinstance(default, str):
                    default = [x.strip() for x in default.split(",") if x.strip()]
                if default is None:
                    default = []
                default = [str(x) for x in default]
            clean["default"] = default
            clean["from_model"] = bool(p.get("from_model"))
            params.append(clean)
        return {"label": label, "logic": logic, "params": params}

    # ---------- writes ----------

    def add(self, name: str, spec: dict):
        templates = self.get_all()
        if name in templates:
            raise ValueError(f"模板已存在: {name}")
        templates[name] = self.validate(name, spec)
        self._save(templates)

    def update(self, name: str, spec: dict):
        templates = self.get_all()
        if name not in templates:
            raise ValueError(f"模板不存在: {name}")
        templates[name] = self.validate(name, spec)
        self._save(templates)

    def delete(self, name: str):
        templates = self.get_all()
        if name not in templates:
            raise ValueError(f"模板不存在: {name}")
        del templates[name]
        self._save(templates)


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
        self._templates = TemplateStore(config_dir)
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
        known_templates = self._templates.get_all()
        rules = []
        for r in raw.get("rules", []):
            template = r.get("template", TEMPLATE_PRESENCE_NEAR_PERSON)
            if template not in known_templates:
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
        # Validate before mutating: ``target`` is shared with the cache, so a
        # failed update must not leave a half-applied rule behind.
        if "template" in fields and fields["template"] not in \
                self._templates.get_all():
            raise ValueError(f"未知模板类型: {fields['template']}")
        for key in ("name", "description", "template", "models", "params",
                    "severity", "enabled"):
            if key in fields:
                setattr(target, key, fields[key])
        self._save(rules)
        return target

    def delete(self, rule_id: int):
        rules = [r for r in self.get_all() if r.id != rule_id]
        if len(rules) == len(self.get_all()):
            raise ValueError(f"规则 {rule_id} 不存在")
        self._save(rules)


# Module-level singleton so main / analyzer / panel share one cache.
_store: RulesStore | None = None
_template_store: TemplateStore | None = None


def get_rules_store(config_dir="config") -> RulesStore:
    global _store
    if _store is None:
        _store = RulesStore(config_dir)
    return _store


def get_template_store(config_dir="config") -> TemplateStore:
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore(config_dir)
    return _template_store


# Backward-compatible helpers ----------------------------------------------

def get_rules_for_camera(active_rule_ids, config_dir="config") -> list[RuleDefinition]:
    return get_rules_store(config_dir).get_rules_for_camera(active_rule_ids)


def get_all_rules(config_dir="config") -> list[RuleDefinition]:
    return sorted(get_rules_store(config_dir).get_all(), key=lambda r: r.id)


def get_template_specs(config_dir="config") -> dict:
    """Template metadata for the panel's dynamic rule edit forms."""
    return get_template_store(config_dir).specs()


def get_template_logics() -> dict:
    """Check logic primitives a new template can bind to (name -> description)."""
    return dict(CHECK_LOGICS)
