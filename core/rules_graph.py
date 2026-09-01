"""
Rule-graph engine - node-graph evaluation for the visual rule canvas.

A rule can be a visual canvas composition instead of a fixed logic primitive:
its template is ``graph`` and the node graph itself lives on the rule's
``graph`` field (see docs/RULE_GRAPH_DESIGN.md). Every frame the graph is
evaluated once in topological order; each node turns its input signals into
``{state: bool, targets: list[Detection]}`` (targets are pixel-space detection
boxes so downstream spatial nodes can do geometry) and the ``alert`` node
emits a Violation on the input's rising edge (prev frame false -> now true).

Evaluation state (duration timers / alert edge memory) is kept per
(camera_id, rule_id, node_id) in a module-level dict.  This keeps temporal
conditions independent when the same rule is assigned to multiple cameras.
"""

import copy
import math
from typing import Dict, Iterable, List, Optional

from core.detector import Detection
from utils.logger import get_logger

logger = get_logger("rules_graph")

# ============================================================
# Node registry (v1: 8 types, docs/RULE_GRAPH_DESIGN.md §4)
# ============================================================
# category: 目标/空间/时间/逻辑/输出 —— 前端积木库按此分组展示。
# params 是参数 schema（name/type/default/desc[/min/max]），前端据此自动生成
# 参数表单；type 约定：string[]（类别多选）/ zones（画面框选）/ float / int。

NODE_TYPES: Dict[str, dict] = {
    "class_present": {
        "label": "类别在场",
        "category": "目标",
        "model_binding": True,
        "inputs": 0,
        "outputs": 1,
        "params": [
            {"name": "classes", "type": "string[]", "default": [],
             "desc": "要监测的类别（必填，任一检出即视为在场）"},
            {"name": "min_confidence", "type": "float", "default": 0.5,
             "min": 0.0, "max": 1.0, "desc": "最低置信度"},
        ],
    },
    "class_covering": {
        "label": "装备覆盖检查",
        "category": "目标",
        "model_binding": True,
        "inputs": 0,
        "outputs": 1,
        "params": [
            {"name": "classes", "type": "string[]", "default": [],
             "desc": "装备类别（必填，如 hardhat）"},
            {"name": "ref_classes", "type": "string[]", "default": ["person"],
             "desc": "参照类别（必填，如 person；全员被覆盖才算通过）"},
            {"name": "coverage_ratio", "type": "float", "default": 0.5,
             "min": 0.0, "max": 1.0,
             "desc": "覆盖比例（装备框与人员框相交面积 / 装备框面积）"},
            {"name": "min_confidence", "type": "float", "default": 0.0,
             "min": 0.0, "max": 1.0, "desc": "最低置信度（0 为不过滤）"},
        ],
    },
    "class_absent": {
        "label": "类别离场",
        "category": "目标",
        "model_binding": True,
        "inputs": 0,
        "outputs": 1,
        "params": [
            {"name": "classes", "type": "string[]", "default": [],
             "desc": "要监测的类别（必填，全部不在场才算通过）"},
            {"name": "min_confidence", "type": "float", "default": 0.5,
             "min": 0.0, "max": 1.0, "desc": "最低置信度（低于该值的检出不算在场）"},
        ],
    },
    "near_class": {
        "label": "靠近参照类别",
        "category": "空间",
        "model_binding": True,
        "inputs": 1,
        "outputs": 1,
        "params": [
            {"name": "ref_classes", "type": "string[]", "default": [],
             "desc": "参照类别（必填，目标框与其检出框相交才算靠近）"},
            {"name": "margin", "type": "float", "default": 0.2,
             "min": 0.0, "max": 2.0,
             "desc": "参照框外扩比例（0 为必须实际相交）"},
        ],
    },
    "in_zone": {
        "label": "在指定区域内",
        "category": "空间",
        "inputs": 1,
        "outputs": 1,
        "params": [
            {"name": "zones", "type": "zones", "default": [],
             "desc": "区域列表（归一化 x/y/w/h，左上角原点，可多个）"},
        ],
    },
    "duration": {
        "label": "持续N秒",
        "category": "时间",
        "inputs": 1,
        "outputs": 1,
        "params": [
            {"name": "seconds", "type": "float", "default": 10,
             "min": 0.0, "desc": "输入连续为真多少秒后输出为真（0 为立即）"},
        ],
    },
    "not": {
        "label": "非",
        "category": "逻辑",
        "inputs": 1,
        "outputs": 1,
        "params": [],
    },
    "and": {
        "label": "且",
        "category": "逻辑",
        "inputs": 2,
        "outputs": 1,
        "params": [],
    },
    "or": {
        "label": "或",
        "category": "逻辑",
        "inputs": 2,
        "outputs": 1,
        "params": [],
    },
    "alert": {
        "label": "告警",
        "category": "输出",
        "inputs": 1,
        "outputs": 1,
        "params": [],
    },
}


def _graph_number(value, *, label: str, integer: bool = False):
    if isinstance(value, bool):
        raise ValueError(f"graph 参数 {label} 必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"graph 参数 {label} 必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"graph 参数 {label} 必须是有限数字")
    if integer:
        if not number.is_integer():
            raise ValueError(f"graph 参数 {label} 必须是整数")
        return int(number)
    return number


def _validate_graph_value(spec: dict, value, *, label: str):
    ptype = spec.get("type")
    if ptype in ("float", "int"):
        number = _graph_number(value, label=label, integer=ptype == "int")
        if spec.get("min") is not None and number < spec["min"]:
            raise ValueError(f"graph 参数 {label} 不能小于 {spec['min']}")
        if spec.get("max") is not None and number > spec["max"]:
            raise ValueError(f"graph 参数 {label} 不能大于 {spec['max']}")
        return number
    if ptype == "string[]":
        if isinstance(value, str):
            value = [x.strip() for x in value.split(",") if x.strip()]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"graph 参数 {label} 必须是字符串列表")
        result = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise ValueError(f"graph 参数 {label} 不能包含空字符串")
            result.append(text)
        return result
    if ptype == "zones":
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"graph 参数 {label} 必须是区域列表")
        result = []
        for zone in value:
            if not isinstance(zone, dict):
                raise ValueError(f"graph 参数 {label} 的区域必须是对象")
            normalized = copy.deepcopy(zone)
            for key in ("x", "y", "w", "h"):
                if key not in zone:
                    raise ValueError(f"graph 参数 {label} 的区域缺少 {key}")
                normalized[key] = _graph_number(zone[key], label=f"{label}.{key}")
            if (normalized["x"] < 0 or normalized["y"] < 0
                    or normalized["w"] <= 0 or normalized["h"] <= 0
                    or normalized["x"] + normalized["w"] > 1
                    or normalized["y"] + normalized["h"] > 1):
                raise ValueError(f"graph 参数 {label} 的区域必须位于 0~1 归一化画面内")
            result.append(normalized)
        return result
    raise ValueError(f"graph 节点参数类型不支持: {ptype}")


def validate_graph(
    graph: dict,
    available_models: Optional[Iterable[str]] = None,
    *,
    require_models: bool = False,
) -> dict:
    """Validate and normalize a graph before it enters the runtime snapshot.

    ``available_models`` is optional to preserve compatibility with callers that
    validate graph structure without a model registry. New rules pass it with
    ``require_models=True`` so every detector node has an explicit model.
    """
    if not isinstance(graph, dict):
        raise ValueError("规则 graph 必须是对象")
    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges", [])
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("graph 至少需要一个节点")
    if not isinstance(raw_edges, list):
        raise ValueError("graph edges 必须是列表")

    normalized = copy.deepcopy(graph)
    nodes = []
    node_map = {}
    available_model_names = (
        {str(name).strip() for name in available_models if str(name).strip()}
        if available_models is not None else None
    )
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ValueError("graph 节点必须是对象")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("graph 节点 id 必须是非空字符串")
        node_id = node_id.strip()
        if node_id in node_map:
            raise ValueError(f"graph 节点 id 重复: {node_id}")
        node_type = node.get("type")
        if node_type not in NODE_TYPES:
            raise ValueError(f"graph 存在未知节点类型: {node_type}")
        params = node.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"graph 节点 {node_id} params 必须是对象")
        schema = NODE_TYPES[node_type]
        model_binding = bool(schema.get("model_binding"))
        raw_model = node.get("model")
        if model_binding:
            if raw_model is None or not str(raw_model).strip():
                if require_models:
                    raise ValueError(f"graph 检测节点 {node_id} 必须选择检测模型")
                model = None
            else:
                model = str(raw_model).strip()
                if available_model_names is not None and model not in available_model_names:
                    raise ValueError(f"graph 节点 {node_id} 引用了不存在的模型: {model}")
        elif raw_model is not None:
            raise ValueError(f"graph 逻辑节点 {node_id} 不支持绑定检测模型")
        else:
            model = None
        param_specs = {p["name"]: p for p in schema.get("params", [])}
        unknown = sorted(set(params) - set(param_specs))
        if unknown:
            raise ValueError(f"graph 节点 {node_id} 包含未知参数: {', '.join(unknown)}")
        clean_params = {name: copy.deepcopy(spec.get("default"))
                        for name, spec in param_specs.items()}
        for name, value in params.items():
            clean_params[name] = _validate_graph_value(
                param_specs[name], value, label=f"{node_id}.{name}"
            )
        clean_node = copy.deepcopy(node)
        clean_node["id"] = node_id
        clean_node["params"] = clean_params
        if model_binding:
            required_params = {
                "class_present": ("classes",),
                "class_absent": ("classes",),
                "class_covering": ("classes", "ref_classes"),
                "near_class": ("ref_classes",),
            }.get(node_type, ())
            for pname in required_params:
                if not clean_params.get(pname):
                    raise ValueError(f"graph 节点 {node_id} 的 {pname} 不能为空")
            if model is None:
                clean_node.pop("model", None)
            else:
                clean_node["model"] = model
        nodes.append(clean_node)
        node_map[node_id] = clean_node

    edges = []
    edge_set = set()
    incoming = {node_id: [] for node_id in node_map}
    outgoing = {node_id: [] for node_id in node_map}
    for edge in raw_edges:
        if not isinstance(edge, dict):
            raise ValueError("graph 边必须是对象")
        src, dst = edge.get("from"), edge.get("to")
        if src not in node_map or dst not in node_map:
            raise ValueError(f"graph 边引用不存在的节点: {src} -> {dst}")
        if src == dst:
            raise ValueError(f"graph 不允许自环: {src}")
        key = (src, dst)
        if key in edge_set:
            raise ValueError(f"graph 存在重复连线: {src} -> {dst}")
        edge_set.add(key)
        incoming[dst].append(src)
        outgoing[src].append(dst)
        edges.append({"from": src, "to": dst})

    for node_id, node in node_map.items():
        expected = int(NODE_TYPES[node["type"]]["inputs"])
        actual = len(incoming[node_id])
        if actual != expected:
            raise ValueError(
                f"graph 节点 {node_id}({node['type']}) 需要 {expected} 个输入，实际 {actual} 个"
            )
    alerts = [node_id for node_id, node in node_map.items() if node["type"] == "alert"]
    if not alerts:
        raise ValueError("graph 必须包含 alert 输出节点")
    if any(outgoing[node_id] for node_id in alerts):
        raise ValueError("alert 节点不能再连接到其他节点")

    # Reuse the same strict topological algorithm as runtime evaluation.
    order, _ = _topo_sort(node_map, edges)
    if order is None:
        raise ValueError("graph 存在环，无法执行")
    normalized["nodes"] = nodes
    normalized["edges"] = edges
    return normalized

# 跨帧记忆：(camera_id, rule_id, node_id) -> duration 连续为真起点
_duration_since: Dict[tuple, float] = {}


def _reset_state():
    """清空全部跨帧记忆（测试辅助；运行态按相机/规则/节点保留）。"""
    _duration_since.clear()


# ============================================================
# Signal helpers
# ============================================================

def _lower_set(names) -> set:
    return {n.lower() for n in (names or [])}


def _false_signal() -> dict:
    return {"state": False, "targets": []}


def _topo_sort(node_map: dict, edges: list):
    """Kahn 拓扑排序。

    返回 (order, incoming)：order 是可求值的节点 id 序列，incoming[node_id]
    是按连线顺序排列的输入节点 id 列表。有环时返回 (None, None)。
    """
    incoming: Dict[object, list] = {nid: [] for nid in node_map}
    outgoing: Dict[object, list] = {nid: [] for nid in node_map}
    for e in edges or []:
        if not isinstance(e, dict):
            continue
        src, dst = e.get("from"), e.get("to")
        if src in incoming and dst in incoming:
            incoming[dst].append(src)
            outgoing[src].append(dst)

    indeg = {nid: len(v) for nid, v in incoming.items()}
    queue = [nid for nid, d in indeg.items() if d == 0]
    order = []
    while queue:
        nid = queue.pop()
        order.append(nid)
        for nxt in outgoing[nid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(incoming):
        return None, None  # 有节点未被处理：图里存在环
    return order, incoming


# ============================================================
# Per-node evaluation (alert handled separately in evaluate_graph)
# ============================================================

def _eval_class_presence(params: dict, detections: List[Detection],
                         absent: bool, model: Optional[str] = None) -> dict:
    """class_present / class_absent：类别在场或全部离场。"""
    if model:
        detections = [d for d in detections if d.model_name == model]
    classes = _lower_set(params.get("classes"))
    if not classes:
        return _false_signal()  # classes 必填，缺省视为不命中
    min_conf = params.get("min_confidence", 0.5)
    min_conf = 0.5 if min_conf is None else float(min_conf)
    hits = [d for d in detections
            if d.class_name.lower() in classes and d.confidence >= min_conf]
    if absent:
        # 全部不在场 -> true；离场信号本身没有目标框
        return {"state": not hits, "targets": []}
    return {"state": bool(hits), "targets": hits}


def _eval_class_covering(params: dict, detections: List[Detection],
                          model: Optional[str] = None) -> dict:
    """class_covering：参照目标在场且**全部**被装备框覆盖（按面积比）。
    语义对齐 AbsenceRequiredCheck：任一参照目标缺装备即 state=false。"""
    if model:
        detections = [d for d in detections if d.model_name == model]
    gear_classes = _lower_set(params.get("classes"))
    ref_classes = _lower_set(params.get("ref_classes"))
    ratio = params.get("coverage_ratio", 0.5)
    ratio = 0.5 if ratio is None else max(0.0, min(1.0, float(ratio)))
    min_conf = params.get("min_confidence", 0.0)
    min_conf = 0.0 if min_conf is None else float(min_conf)
    refs = [d for d in detections
            if d.class_name.lower() in ref_classes and d.confidence >= min_conf]
    if not refs:
        return _false_signal()  # 参照不在场：不构成缺装备告警
    gear = [d for d in detections
            if d.class_name.lower() in gear_classes and d.confidence >= min_conf]
    covered = all(any(_covers(ref.bbox, g.bbox, ratio) for g in gear)
                  for ref in refs)
    return {"state": covered, "targets": refs}


def _covers(ref_bbox: tuple, gear_bbox: tuple, ratio: float) -> bool:
    """装备框与参照框相交面积 ≥ ratio×装备框面积 → 覆盖成立。"""
    rx1, ry1, rx2, ry2 = ref_bbox
    gx1, gy1, gx2, gy2 = gear_bbox
    ix = max(0.0, min(rx2, gx2) - max(rx1, gx1))
    iy = max(0.0, min(ry2, gy2) - max(ry1, gy1))
    gear_area = max((gx2 - gx1) * (gy2 - gy1), 1e-6)
    return ix * iy / gear_area > ratio


def _center_in_zones(bbox: tuple, zones: list, fw: float, fh: float) -> bool:
    """True if the detection center point falls inside any zone rect."""
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    for z in zones:
        try:
            zx, zy = float(z["x"]) * fw, float(z["y"]) * fh
            zw, zh = float(z["w"]) * fw, float(z["h"]) * fh
        except (KeyError, TypeError, ValueError):
            continue
        if zx <= cx <= zx + zw and zy <= cy <= zy + zh:
            return True
    return False


def _eval_near_class(params: dict, input_signal: dict,
                     detections: List[Detection], model: Optional[str] = None) -> dict:
    """near_class：输入 targets 与参照类别检出框（按 margin 外扩）相交。"""
    if model:
        detections = [d for d in detections if d.model_name == model]
    targets = [d for d in (input_signal.get("targets") or [])
               if not model or d.model_name == model]
    if not targets:
        return _false_signal()
    ref_classes = _lower_set(params.get("ref_classes"))
    if not ref_classes:
        return _false_signal()  # 参照类别必填，缺省视为不命中
    margin = params.get("margin", 0.2)
    margin = 0.2 if margin is None else max(0.0, float(margin))
    refs = [d for d in detections if d.class_name.lower() in ref_classes]
    kept = [d for d in targets if any(_bboxes_touch(d.bbox, r.bbox, margin)
                                      for r in refs)]
    return {"state": bool(kept), "targets": kept}


def _bboxes_touch(a: tuple, b: tuple, margin: float) -> bool:
    """True if box a intersects box b expanded by margin of b's size."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    mx = (bx2 - bx1) * margin
    my = (by2 - by1) * margin
    return ax1 < bx2 + mx and ax2 > bx1 - mx and ay1 < by2 + my and ay2 > by1 - my


def _eval_in_zone(params: dict, input_signal: dict,
                  frame_size: Optional[tuple]) -> dict:
    """in_zone：输入 targets 的中心点落在任一 zone 内（归一化换算像素）。"""
    zones = [z for z in (params.get("zones") or []) if isinstance(z, dict)]
    targets = input_signal.get("targets") or []
    if not zones or not targets or not frame_size:
        return _false_signal()
    fw, fh = float(frame_size[0]), float(frame_size[1])
    inside = [d for d in targets if _center_in_zones(d.bbox, zones, fw, fh)]
    return {"state": bool(inside), "targets": inside}


def _eval_duration(params: dict, input_signal: dict, timestamp: float,
                   key: tuple) -> dict:
    """duration：输入连续为真计时，达到 seconds 才输出 true；中断即清零。"""
    seconds = params.get("seconds", 10)
    seconds = 10.0 if seconds is None else max(0.0, float(seconds))
    if not input_signal.get("state"):
        _duration_since.pop(key, None)  # 中断即清零
        return _false_signal()
    since = _duration_since.setdefault(key, timestamp)
    if timestamp - since >= seconds:
        # targets 原样透传
        return {"state": True,
                "targets": list(input_signal.get("targets") or [])}
    return _false_signal()


def _eval_node(node_id, node: dict, inputs: List[dict],
               detections: List[Detection], frame_size: Optional[tuple],
               timestamp: float, rule_id: int, camera_id: str = "") -> dict:
    """求值单个非 alert 节点，返回信号 {state: bool, targets: list}。"""
    ntype = node["type"]
    if NODE_TYPES[ntype]["inputs"] > 0 and not inputs:
        return _false_signal()  # 必需输入未连线：视为恒假，避免空图误报
    params = node.get("params") or {}
    model = node.get("model")

    if ntype in ("class_present", "class_absent"):
        return _eval_class_presence(
            params, detections, absent=(ntype == "class_absent"), model=model,
        )
    if ntype == "class_covering":
        return _eval_class_covering(params, detections, model=model)
    if ntype == "in_zone":
        return _eval_in_zone(params, inputs[0], frame_size)
    if ntype == "near_class":
        return _eval_near_class(params, inputs[0], detections, model=model)
    if ntype == "duration":
        return _eval_duration(params, inputs[0], timestamp,
                              key=(camera_id, rule_id, node_id))
    if ntype == "not":
        # state 取反，targets 清空
        return {"state": not inputs[0].get("state", False), "targets": []}

    # and / or：state 取与/或，targets 取第一个有 targets 的输入
    states = [bool(s.get("state")) for s in inputs]
    state = all(states) if ntype == "and" else any(states)
    targets = next((list(s.get("targets") or []) for s in inputs
                    if s.get("targets")), [])
    return {"state": state, "targets": targets}


def _make_violation(rule_id: int, rule, timestamp: float,
                    det: Optional[Detection], camera_id: str = ""):
    """构造 Violation（det 为空时 bbox=None、confidence=0）。

    core.analyzer 在模块级导入本模块，故 Violation 延迟导入避免循环依赖。
    """
    from core.analyzer import Violation

    return Violation(
        camera_id=camera_id,
        rule_id=rule_id,
        rule_name=str(getattr(rule, "name", "") or f"rule_{rule_id}"),
        description=str(getattr(rule, "description", "") or ""),
        confidence=float(det.confidence) if det is not None else 0.0,
        severity=int(getattr(rule, "severity", 2) or 2),
        timestamp=timestamp,
        bbox=det.bbox if det is not None else None,
    )


def _eval_alert(node_id, input_signal: dict, timestamp: float, rule_id: int,
                rule, camera_id: str):
    """alert：消耗信号不产生输出；输入为真即产出 Violation。

    持续违规时每帧都产出，由 analyzer 的冷却时间节流为每 cooldown 秒一条
    ——与旧版固定判定的行为完全一致（持续违规会反复提醒直到处理）。
    bbox 取该信号 targets 中置信度最高者，targets 为空则 bbox=None。
    """
    state = bool(input_signal.get("state"))
    if not state:
        return None
    targets = input_signal.get("targets") or []
    best = max(targets, key=lambda d: d.confidence) if targets else None
    return _make_violation(rule_id, rule, timestamp, best, camera_id)


# ============================================================
# Main entry
# ============================================================

def evaluate_graph(
    graph: dict,
    detections: List[Detection],
    frame_size: Optional[tuple],
    timestamp: float,
    rule_id: int,
    rule=None,
    camera_id: str = "",
) -> Optional["Violation"]:
    """对一个规则图做一次拓扑序求值（契约 docs/RULE_GRAPH_DESIGN.md §3/§5）。

    返回本帧产生的 Violation（alert 上升沿触发），无告警返回 None；
    graph 缺失/为空/含未知节点类型/有环时整图跳过（返回 None，有环记 warning）。

    ``rule`` 用于填充告警的名称/描述/严重度（RuleDefinition 或兼容对象，
    为 None 时回退到 rule_id）；``camera_id`` 透传到 Violation.camera_id。
    """
    if not isinstance(graph, dict) or not graph:
        return None
    raw_nodes = graph.get("nodes") or []
    if not raw_nodes:
        return None

    node_map = {}
    for n in raw_nodes:
        nid = n.get("id") if isinstance(n, dict) else None
        if not isinstance(nid, (str, int)) or n.get("type") not in NODE_TYPES:
            logger.warning(f"规则 {rule_id} 的 graph 节点非法或类型未知: {n!r}")
            return None
        node_map[nid] = n

    order, incoming = _topo_sort(node_map, graph.get("edges"))
    if order is None:
        logger.warning(f"规则 {rule_id} 的 graph 存在环，整图跳过")
        return None

    signals: Dict[object, dict] = {}
    for nid in order:
        node = node_map[nid]
        inputs = [signals.get(src) or _false_signal() for src in incoming[nid]]
        if node["type"] == "alert":
            violation = _eval_alert(
                nid, inputs[0] if inputs else _false_signal(),
                timestamp, rule_id, rule, camera_id,
            )
            if violation is not None:
                return violation
            signals[nid] = _false_signal()  # alert 消耗信号，无输出
        else:
            signals[nid] = _eval_node(
                nid, node, inputs, detections, frame_size, timestamp, rule_id,
                camera_id,
            )
    return None
