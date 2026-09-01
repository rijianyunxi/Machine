"""Pure rule and template definitions used by the runtime and repository.

This module deliberately contains no persistence or YAML I/O.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field

TEMPLATE_PPE_ABSENCE = "ppe_absence"
TEMPLATE_PRESENCE_NEAR_PERSON = "presence_near_person"

LOGIC_PRESENCE = "presence"
LOGIC_PRESENCE_NEAR = "presence_near"
LOGIC_ABSENCE_REQUIRED = "absence_required"
LOGIC_ZONE_INTRUSION = "zone_intrusion"
LOGIC_GRAPH = "graph"

LOGIC_LABELS = {
    LOGIC_PRESENCE: "出现即告警",
    LOGIC_PRESENCE_NEAR: "靠近人员才告警",
    LOGIC_ABSENCE_REQUIRED: "装备缺失检查",
    LOGIC_ZONE_INTRUSION: "区域侵入告警",
    LOGIC_GRAPH: "画布自定义组合",
}

CHECK_LOGICS = {
    LOGIC_PRESENCE: "画面中出现所选类别就告警",
    LOGIC_PRESENCE_NEAR: "所选类别检出且靠近人员才告警",
    LOGIC_ABSENCE_REQUIRED: "人员未佩戴必需装备或检出违规类别时告警",
    LOGIC_ZONE_INTRUSION: "所选类别出现在告警区域内才告警",
    LOGIC_GRAPH: "由可视化画布的节点图组合判定",
}

_PARAM_TYPES = ("classes", "list", "float", "int", "zones")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _number(value, *, pname: str, integer: bool) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"参数 {pname} 必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {pname} 必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"参数 {pname} 必须是有限数字")
    if integer:
        if not number.is_integer():
            raise ValueError(f"参数 {pname} 必须是整数")
        return int(number)
    return number


def _normalize_zone(zone, *, pname: str) -> dict:
    if not isinstance(zone, dict):
        raise ValueError(f"参数 {pname} 的区域必须是对象")
    values = {}
    for key in ("x", "y", "w", "h"):
        if key not in zone:
            raise ValueError(f"参数 {pname} 的区域缺少 {key}")
        values[key] = _number(zone[key], pname=f"{pname}.{key}", integer=False)
    if values["x"] < 0 or values["y"] < 0 or values["w"] <= 0 or values["h"] <= 0:
        raise ValueError(f"参数 {pname} 的区域坐标必须满足 x/y>=0 且 w/h>0")
    if values["x"] + values["w"] > 1 or values["y"] + values["h"] > 1:
        raise ValueError(f"参数 {pname} 的区域必须位于 0~1 归一化画面内")
    # Keep optional labels/metadata, but never allow arbitrary coordinate types.
    result = copy.deepcopy(zone)
    result.update(values)
    return result


def _normalize_param_value(param: dict, value):
    pname = param["name"]
    ptype = param["type"]
    if ptype in ("float", "int"):
        number = _number(value, pname=pname, integer=ptype == "int")
        if "min" in param and number < param["min"]:
            raise ValueError(f"参数 {pname} 不能小于 {param['min']}")
        if "max" in param and number > param["max"]:
            raise ValueError(f"参数 {pname} 不能大于 {param['max']}")
        return number
    if ptype == "zones":
        if value is None:
            return []
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"参数 {pname} 必须是区域列表")
        return [_normalize_zone(item, pname=pname) for item in value]
    if ptype in ("classes", "list"):
        if isinstance(value, str):
            value = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"参数 {pname} 必须是列表")
        if ptype == "classes":
            result = []
            for item in value:
                text = str(item).strip()
                if not text:
                    raise ValueError(f"参数 {pname} 不能包含空类别")
                result.append(text)
            return result
        return copy.deepcopy(list(value))
    raise ValueError(f"参数 {pname} 类型不支持: {ptype}")


def validate_template(name: str, spec: dict) -> dict:
    """Normalize and validate one template definition."""
    if not _NAME_RE.match(name or ""):
        raise ValueError("模板名只能使用小写字母/数字/下划线，且以字母开头")
    if not isinstance(spec, dict):
        raise ValueError("模板定义必须是对象")
    label = str(spec.get("label") or "").strip()
    if not label:
        raise ValueError("模板显示名称不能为空")
    logic = str(spec.get("logic") or "").strip()
    if logic not in CHECK_LOGICS:
        raise ValueError(f"未知判定逻辑: {logic}，可选: {', '.join(CHECK_LOGICS)}")
    raw_params = spec.get("params") or []
    if not isinstance(raw_params, (list, tuple)):
        raise ValueError("params 必须是参数定义列表")
    params, seen = [], set()
    for p in raw_params:
        if not isinstance(p, dict):
            raise ValueError(f"参数定义必须是对象: {p!r}")
        pname = str(p.get("name") or "").strip()
        if not _NAME_RE.match(pname):
            raise ValueError(f"参数名不合法: {pname!r}")
        if pname in seen:
            raise ValueError(f"参数名重复: {pname}")
        seen.add(pname)
        ptype = str(p.get("type") or "classes")
        if ptype not in _PARAM_TYPES:
            raise ValueError(f"参数 {pname} 类型不支持: {ptype}")
        clean = {"name": pname, "type": ptype, "desc": str(p.get("desc") or pname)}
        if ptype in ("float", "int"):
            for bound in ("min", "max"):
                if p.get(bound) is not None:
                    clean[bound] = _number(p[bound], pname=f"{pname}.{bound}", integer=False)
            if "min" in clean and "max" in clean and clean["min"] > clean["max"]:
                raise ValueError(f"参数 {pname} 的最小值不能大于最大值")
            default = p.get("default")
            if default is None:
                raise ValueError(f"参数 {pname} 缺少默认值")
            clean["default"] = _normalize_param_value(clean, default)
        elif ptype == "zones":
            clean["default"] = _normalize_param_value(clean, p.get("default") or [])
        else:
            clean["default"] = _normalize_param_value(clean, p.get("default") or [])
        clean["from_model"] = bool(p.get("from_model"))
        params.append(clean)
    return {"label": label, "logic": logic, "params": params}


def validate_rule_params(template_spec: dict, values: dict | None) -> dict:
    """Return a normalized complete parameter object for one rule instance.

    Unknown keys are rejected so a typo cannot silently disable a safety rule.
    Missing keys receive the template defaults. This same function is used for
    camera-level overrides, which means overrides are validated independently
    before being merged into a runtime snapshot.
    """
    if not isinstance(values or {}, dict):
        raise ValueError("规则 params 必须是对象")
    spec = validate_template("runtime_template", template_spec)
    definitions = {p["name"]: p for p in spec["params"]}
    unknown = sorted(set(values or {}) - set(definitions))
    if unknown:
        raise ValueError(f"规则包含未定义参数: {', '.join(unknown)}")
    result = {p["name"]: copy.deepcopy(p["default"]) for p in spec["params"]}
    for name, value in (values or {}).items():
        result[name] = _normalize_param_value(definitions[name], value)
    return result


@dataclass
class RuleDefinition:
    """A runtime rule instance loaded from the repository."""

    id: int
    name: str
    description: str
    category: str = "ppe"
    template: str = TEMPLATE_PRESENCE_NEAR_PERSON
    models: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)
    severity: int = 2
    enabled: bool = True
    revision: int = 1


def template_logics() -> dict:
    return {key: {"label": LOGIC_LABELS[key], "desc": desc}
            for key, desc in CHECK_LOGICS.items()}

