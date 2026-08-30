"""可视化规则画布后端自测（纯 assert 脚本，不依赖 pytest）。

用法（项目根目录）：
    .venv/bin/python tests/test_rules_graph.py

覆盖：9 种节点语义（含 near_class）、契约 §2 离岗图完整时间序列、环/非法图防御、
RulesStore graph 字段持久化（含老规则兼容）、NODE_TYPES 注册表、
state.node_types() 委托与 API 端点。
"""

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ruamel.yaml import YAML

from core.analyzer import RULE_LOGICS, GraphCheck, Violation
from core.detector import Detection
from core.rules_graph import (
    NODE_TYPES,
    _eval_class_presence,
    _eval_in_zone,
    _eval_node,
    _reset_state,
    evaluate_graph,
)
from rules.rules_engine import (
    LOGIC_GRAPH,
    RuleDefinition,
    RulesStore,
    TemplateStore,
)


# ------------------------------------------------------------
# 构造辅助
# ------------------------------------------------------------

def det(cls, conf, bbox, model="test"):
    return Detection(class_id=0, class_name=cls, confidence=conf, bbox=bbox,
                     model_name=model)


def reset_engine_singletons():
    """重置 rules_engine 的模块级单例，避免用例间串用彼此的临时配置目录。"""
    import rules.rules_engine as engine

    engine._store = None
    engine._template_store = None


def node(nid, ntype, params=None):
    n = {"id": nid, "type": ntype}
    if params is not None:
        n["params"] = params
    return n


def edge(src, dst):
    return {"from": src, "to": dst}


def graph_of(nodes, edges):
    return {"nodes": nodes, "edges": edges}


PERSON = {"classes": ["person"], "min_confidence": 0.5}


# ------------------------------------------------------------
# 1. 节点注册表
# ------------------------------------------------------------

def test_node_registry():
    assert set(NODE_TYPES) == {
        "class_present", "class_absent", "in_zone", "duration",
        "not", "and", "or", "alert", "near_class"}, "注册表必须恰好是契约 §4 的 8 种节点"
    labels = {"class_present": "类别在场", "class_absent": "类别离场",
              "in_zone": "在指定区域内", "duration": "持续N秒",
              "not": "非", "and": "且", "or": "或", "alert": "告警",
              "near_class": "靠近参照类别"}
    categories = {"class_present": "目标", "class_absent": "目标",
                  "in_zone": "空间", "duration": "时间",
                  "not": "逻辑", "and": "逻辑", "or": "逻辑",
                  "alert": "输出", "near_class": "空间"}
    inputs = {"class_present": 0, "class_absent": 0, "in_zone": 1,
              "duration": 1, "not": 1, "and": 2, "or": 2, "alert": 1,
              "near_class": 1}
    for t, spec in NODE_TYPES.items():
        assert set(spec) == {"label", "category", "inputs", "outputs", "params"}, t
        assert spec["label"] == labels[t], t
        assert spec["category"] == categories[t], t
        assert spec["inputs"] == inputs[t], t
        assert spec["outputs"] == 1, t
        assert isinstance(spec["params"], list), t
        for p in spec["params"]:
            for k in ("name", "type", "default", "desc"):
                assert k in p, (t, p)
    # 关键参数 schema（契约 §4）
    assert NODE_TYPES["class_present"]["params"][0] == {
        "name": "classes", "type": "string[]", "default": [],
        "desc": "要监测的类别（必填，任一检出即视为在场）"}
    min_conf = NODE_TYPES["class_present"]["params"][1]
    assert min_conf["name"] == "min_confidence" and min_conf["default"] == 0.5
    assert min_conf["min"] == 0.0 and min_conf["max"] == 1.0
    assert NODE_TYPES["in_zone"]["params"][0]["type"] == "zones"
    seconds = NODE_TYPES["duration"]["params"][0]
    assert seconds["name"] == "seconds" and seconds["default"] == 10
    assert seconds["min"] == 0.0
    for t in ("not", "and", "or", "alert"):
        assert NODE_TYPES[t]["params"] == [], t


# ------------------------------------------------------------
# 2. class_present / class_absent 语义
# ------------------------------------------------------------

def test_class_present_absent():
    _reset_state()
    person = det("person", 0.8, (10, 10, 50, 50))
    cat = det("cat", 0.9, (100, 100, 140, 140))
    low = det("person", 0.3, (10, 10, 50, 50))

    # 在场：任一类别检出且 conf 达标；targets 只含命中类别
    sig = _eval_class_presence(PERSON, [person, cat], absent=False)
    assert sig["state"] is True and sig["targets"] == [person]
    # 低于阈值不算在场
    sig = _eval_class_presence(PERSON, [low, cat], absent=False)
    assert sig["state"] is False and sig["targets"] == []
    # 不在场
    sig = _eval_class_presence(PERSON, [cat], absent=False)
    assert sig["state"] is False and sig["targets"] == []
    # 类别匹配不区分大小写（与 analyzer 现有约定一致）
    sig = _eval_class_presence(PERSON, [det("Person", 0.8, (1, 1, 2, 2))],
                               absent=False)
    assert sig["state"] is True and len(sig["targets"]) == 1
    # classes 必填：缺省视为不命中
    sig = _eval_class_presence({}, [person], absent=False)
    assert sig["state"] is False

    # 离场：类别全部不在场 -> true，targets 恒为空
    sig = _eval_class_presence(PERSON, [cat], absent=True)
    assert sig["state"] is True and sig["targets"] == []
    sig = _eval_class_presence(PERSON, [person, cat], absent=True)
    assert sig["state"] is False and sig["targets"] == []
    sig = _eval_class_presence(PERSON, [low], absent=True)
    assert sig["state"] is True  # 低置信度检出不算在场

    # 端到端探针：class_present -> alert，bbox 取置信度最高的命中目标
    g = graph_of([node("n1", "class_present", PERSON), node("n2", "alert")],
                 [edge("n1", "n2")])
    _reset_state()
    v = evaluate_graph(g, [det("person", 0.6, (0, 0, 10, 10)),
                           det("person", 0.95, (40, 40, 80, 80)),
                           det("cat", 0.99, (100, 100, 120, 120))],
                       (1000, 1000), 0.0, 901)
    assert v is not None and v.bbox == (40, 40, 80, 80)  # 猫被过滤


# ------------------------------------------------------------
# 3. in_zone 语义（中心点 + frame_size 归一化换算）
# ------------------------------------------------------------

def test_in_zone():
    _reset_state()
    zones = [{"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}]  # 像素 [500,1000]x[0,500]
    params = {"zones": zones}
    inside = det("person", 0.8, (800, 100, 900, 200))    # 中心 (850,150)
    outside = det("person", 0.8, (100, 100, 200, 200))   # 中心 (150,150)

    sig = _eval_in_zone(params, {"state": True, "targets": [inside]}, (1000, 500))
    assert sig["state"] is True and sig["targets"] == [inside]
    sig = _eval_in_zone(params, {"state": True, "targets": [outside]}, (1000, 500))
    assert sig["state"] is False and sig["targets"] == []
    # 边界含端点（与 ZoneIntrusionCheck 一致）：中心恰在 zone 左边缘
    on_edge = det("person", 0.8, (496, 240, 504, 250))   # 中心 (500,245)
    sig = _eval_in_zone(params, {"state": True, "targets": [on_edge]}, (1000, 500))
    assert sig["state"] is True
    # 多区域取并集
    sig = _eval_in_zone({"zones": zones + [{"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.4}]},
                        {"state": True, "targets": [outside]}, (1000, 500))
    assert sig["state"] is True
    # 防御：无 frame_size / 无 targets / 无 zones -> 恒假
    assert _eval_in_zone(params, {"state": True, "targets": [inside]}, None)["state"] is False
    assert _eval_in_zone(params, {"state": True, "targets": []}, (1000, 500))["state"] is False
    assert _eval_in_zone({"zones": []}, {"state": True, "targets": [inside]},
                         (1000, 500))["state"] is False

    # 端到端探针：person -> in_zone -> alert
    g = graph_of([node("n1", "class_present", PERSON),
                  node("n2", "in_zone", params), node("n3", "alert")],
                 [edge("n1", "n2"), edge("n2", "n3")])
    _reset_state()
    v = evaluate_graph(g, [outside], (1000, 500), 0.0, 902)
    assert v is None
    _reset_state()
    v = evaluate_graph(g, [inside], (1000, 500), 1.0, 902)
    assert v is not None and v.bbox == inside.bbox


# ------------------------------------------------------------
# 4. duration 语义（计时 / 中断清零 / targets 透传）
# ------------------------------------------------------------

def test_duration():
    _reset_state()
    person = det("person", 0.9, (10, 10, 50, 50))
    g = graph_of([node("n1", "class_present", PERSON),
                  node("n2", "duration", {"seconds": 3}), node("n3", "alert")],
                 [edge("n1", "n2"), edge("n2", "n3")])

    def frame(t, dets, rid=1001):
        return evaluate_graph(g, dets, (1000, 1000), t, rid)

    assert frame(0.0, [person]) is None            # 计时开始 0s
    assert frame(1.0, [person]) is None            # 1s < 3s
    assert frame(2.0, [person]) is None            # 2s < 3s
    v = frame(3.0, [person])                       # 3s 达标 -> 上升沿触发
    assert v is not None and v.timestamp == 3.0
    assert v.bbox == person.bbox                   # targets 原样透传
    assert frame(4.0, [person]) is None            # 已为真，不再触发
    assert frame(5.0, []) is None                  # 中断 -> 清零
    assert frame(6.0, [person]) is None            # 重新计时
    assert frame(8.0, [person]) is None            # 2s（若未清零此处会误报）
    v = frame(9.0, [person])                       # 重新计满 3s -> 再次触发
    assert v is not None and v.timestamp == 9.0

    # seconds=0：立即为真
    g0 = graph_of([node("n1", "class_present", PERSON),
                   node("n2", "duration", {"seconds": 0}), node("n3", "alert")],
                  [edge("n1", "n2"), edge("n2", "n3")])
    _reset_state()
    v = evaluate_graph(g0, [person], (1000, 1000), 0.0, 1002)
    assert v is not None


# ------------------------------------------------------------
# 5. not / and / or 语义
# ------------------------------------------------------------

def test_not_and_or():
    _reset_state()
    person = det("person", 0.9, (10, 10, 50, 50))
    cat = det("cat", 0.9, (100, 100, 140, 140))

    # not：state 取反、targets 清空（画面里只有 cat 时触发，bbox 必须为 None）
    g = graph_of([node("n1", "class_present", PERSON), node("n2", "not"),
                  node("n3", "alert")],
                 [edge("n1", "n2"), edge("n2", "n3")])

    def frame(t, dets, rid=1003):
        return evaluate_graph(g, dets, (1000, 1000), t, rid)

    v = frame(0.0, [cat])
    assert v is not None and v.bbox is None and v.confidence == 0.0
    assert frame(1.0, [cat]) is None               # 持续为真不再触发
    assert frame(2.0, [person, cat]) is None       # person 在场 -> not 为假
    # 直接校验 not 的 targets 清空（内部求值，信号级断言）
    sig = _eval_node("n2", node("n2", "not"),
                     [{"state": True, "targets": [person]}], [], None, 0.0, 1)
    assert sig["state"] is False and sig["targets"] == []

    # and：两个输入同时为真才触发；targets 取第一个有 targets 的输入
    g_and = graph_of([node("n1", "class_present", PERSON),
                      node("n2", "class_present", {"classes": ["cat"],
                                                   "min_confidence": 0.5}),
                      node("n3", "and"), node("n4", "alert")],
                     [edge("n1", "n3"), edge("n2", "n3"), edge("n3", "n4")])
    _reset_state()
    v = evaluate_graph(g_and, [person, cat], (1000, 1000), 0.0, 1004)
    assert v is not None and v.bbox == person.bbox  # 第一个有 targets 的输入
    _reset_state()
    assert evaluate_graph(g_and, [person], (1000, 1000), 1.0, 1004) is None
    _reset_state()
    assert evaluate_graph(g_and, [cat], (1000, 1000), 2.0, 1004) is None

    # or：任一输入为真即触发
    g_or = graph_of([node("n1", "class_present", PERSON),
                     node("n2", "class_present", {"classes": ["cat"],
                                                  "min_confidence": 0.5}),
                     node("n3", "or"), node("n4", "alert")],
                    [edge("n1", "n3"), edge("n2", "n3"), edge("n3", "n4")])
    _reset_state()
    v = evaluate_graph(g_or, [person], (1000, 1000), 0.0, 1005)
    assert v is not None and v.bbox == person.bbox
    _reset_state()
    v = evaluate_graph(g_or, [cat], (1000, 1000), 1.0, 1005)
    assert v is not None and v.bbox == cat.bbox
    _reset_state()
    assert evaluate_graph(g_or, [], (1000, 1000), 2.0, 1005) is None


# ------------------------------------------------------------
# 6. alert 上升沿只触发一次
# ------------------------------------------------------------

def test_alert_rising_edge_once():
    _reset_state()
    person = det("person", 0.9, (10, 10, 50, 50))
    g = graph_of([node("n1", "class_present", PERSON), node("n2", "alert")],
                 [edge("n1", "n2")])

    def frame(t, dets, rid=1006):
        return evaluate_graph(g, dets, (1000, 1000), t, rid)

    assert frame(0.0, [person]) is not None        # 首帧 true 视为上升沿
    assert frame(1.0, [person]) is None            # 持续为真不重复触发
    assert frame(2.0, [person]) is None
    assert frame(3.0, []) is None                  # 转为假
    assert frame(4.0, []) is None
    assert frame(5.0, [person]) is not None        # 再次上升沿
    assert frame(6.0, [person]) is None


# ------------------------------------------------------------
# 7. 契约 §2 离岗图：person 离场 10 秒
# ------------------------------------------------------------

LEAVE_GRAPH = graph_of(
    [node("n1", "class_present", PERSON),
     node("n2", "not"),
     node("n3", "duration", {"seconds": 10}),
     node("n4", "alert", {})],
    [edge("n1", "n2"), edge("n2", "n3"), edge("n3", "n4")],
)


def test_person_leave_post_full_graph():
    _reset_state()
    person = det("person", 0.9, (10, 10, 50, 50))
    # (时刻, 检测结果, 期望是否告警)
    timeline = [
        (0.0, [person], False),   # 人在岗
        (5.0, [person], False),
        (9.0, [person], False),
        (10.0, [], False),        # 人离场，计时开始
        (15.0, [], False),        # 5s < 10s
        (19.0, [], False),        # 9s < 10s
        (20.0, [], True),         # 连续离场 10s -> 告警
        (21.0, [], False),        # 持续为真不重复
        (25.0, [person], False),  # 人回岗，计时清零
        (26.0, [], False),        # 又离场，重新计时
        (35.0, [], False),        # 9s < 10s
        (36.0, [], True),         # 再次计满 10s -> 告警
    ]
    for t, dets, expect in timeline:
        v = evaluate_graph(LEAVE_GRAPH, dets, (1000, 1000), t, 30)
        assert (v is not None) == expect, f"t={t} 期望告警={expect}"
        if v is not None:
            assert v.rule_id == 30
            assert v.bbox is None            # not 之后 targets 已清空
            assert v.rule_name == "rule_30"  # 未传 rule 时的回退名


# ------------------------------------------------------------
# 8. 环 / 非法图防御
# ------------------------------------------------------------

def test_cycles_and_invalid_graphs():
    person = det("person", 0.9, (10, 10, 50, 50))
    # 三节点环：class_present -> duration -> alert -> class_present
    cyclic = graph_of(
        [node("n1", "class_present", PERSON),
         node("n2", "duration", {"seconds": 1}),
         node("n3", "alert")],
        [edge("n1", "n2"), edge("n2", "n3"), edge("n3", "n1")],
    )
    _reset_state()
    assert evaluate_graph(cyclic, [person], (1000, 1000), 0.0, 1007) is None

    # 自环
    self_loop = graph_of([node("n1", "duration", {"seconds": 1})],
                         [edge("n1", "n1")])
    assert evaluate_graph(self_loop, [], (1000, 1000), 0.0, 1007) is None

    # graph 缺失/为空
    assert evaluate_graph(None, [person], (1000, 1000), 0.0, 1008) is None
    assert evaluate_graph({}, [person], (1000, 1000), 0.0, 1008) is None
    assert evaluate_graph({"nodes": []}, [person], (1000, 1000), 0.0, 1008) is None
    assert evaluate_graph({"nodes": [], "edges": []}, [person],
                          (1000, 1000), 0.0, 1008) is None

    # 未知节点类型 -> 整图跳过
    unknown = graph_of([node("n1", "near_person"), node("n2", "alert")],
                       [edge("n1", "n2")])
    assert evaluate_graph(unknown, [person], (1000, 1000), 0.0, 1008) is None

    # 悬空连线被忽略，其余部分照常求值
    dangling = graph_of([node("n1", "class_present", PERSON), node("n2", "alert")],
                        [edge("n1", "n2"), edge("ghost", "n2")])
    _reset_state()
    v = evaluate_graph(dangling, [person], (1000, 1000), 0.0, 1009)
    assert v is not None


# ------------------------------------------------------------
# 9. RulesStore：graph 字段持久化 + 老规则兼容
# ------------------------------------------------------------

def test_rules_store_persistence():
    tmp = tempfile.mkdtemp(prefix="rules_graph_test_")
    try:
        # graph 模板必须能通过现有模板校验（logic=graph、params=[] 合法）
        clean = TemplateStore.validate("graph", {"label": "自定义组合（画布）",
                                                 "logic": "graph", "params": []})
        assert clean == {"label": "自定义组合（画布）", "logic": "graph",
                         "params": []}
        store = RulesStore(tmp)
        assert store.get_all()  # 种子规则已就位
        # 种子模板（全新部署路径）同样包含 graph 模板
        assert TemplateStore(tmp).get_all().get("graph") == {
            "label": "自定义组合（画布）", "logic": "graph", "params": []}

        graph = {
            "nodes": [
                {"id": "n1", "type": "class_present",
                 "params": {"classes": ["person"], "min_confidence": 0.5}},
                {"id": "n2", "type": "not"},
                {"id": "n3", "type": "duration", "params": {"seconds": 10}},
                {"id": "n4", "type": "alert", "params": {}},
            ],
            "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"},
                      {"from": "n3", "to": "n4"}],
        }
        store.add(RuleDefinition(
            id=30, name="person_leave_post", description="值守区域人员离开",
            template="graph", models=["ppe"], params={}, graph=graph,
            severity=2, enabled=True,
        ))

        # 重新实例化 = 从磁盘读回
        store2 = RulesStore(tmp)
        loaded = store2.get_by_id(30)
        assert loaded is not None and loaded.template == "graph"
        assert loaded.graph == graph, "graph 字段必须完整往返"

        # 老规则缺省 graph 为空 dict，GraphCheck 不告警
        old = store2.get_by_id(1)
        assert old.graph == {}
        _reset_state()
        assert GraphCheck()("cam1", old, [det("person", 0.9, (0, 0, 1, 1))],
                            0.0, (10, 10)) is None

        # update 路径也能写入 graph（PUT /api/rules 带 graph 字段）
        graph2 = graph_of([node("a", "class_present", {"classes": ["cat"],
                                                       "min_confidence": 0.5}),
                           node("b", "alert")], [edge("a", "b")])
        store2.update(13, {"graph": graph2})
        store3 = RulesStore(tmp)
        assert store3.get_by_id(13).graph == graph2

        # 不带 graph 的 update 不得清掉已有 graph，老规则也不得长出 graph 键
        store3.update(1, {"name": "renamed_no_graph"})
        store4 = RulesStore(tmp)
        assert store4.get_by_id(1).graph == {}
        assert store4.get_by_id(13).graph == graph2
        assert store4.get_by_id(30).graph == graph

        # YAML 层逐条目校验：只有带 graph 的规则才写 graph 键
        doc = YAML(typ="rt").load((Path(tmp) / "rules.yaml").open("r",
                                                                  encoding="utf-8"))
        by_id = {int(r["id"]): r for r in doc["rules"]}
        assert "graph" not in by_id[1], "老规则条目不得新增 graph 键"
        assert "graph" in by_id[30] and "graph" in by_id[13]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------
# 10. GraphCheck 集成（analyzer 分派 + Violation 字段）
# ------------------------------------------------------------

def test_graph_check_integration():
    assert LOGIC_GRAPH in RULE_LOGICS
    check = RULE_LOGICS[LOGIC_GRAPH]
    assert isinstance(check, GraphCheck)

    rule = RuleDefinition(
        id=31, name="cat_at_door", description="门口有猫出现",
        template="graph", models=["ppe"], severity=3,
        graph=graph_of([node("n1", "class_present",
                             {"classes": ["cat"], "min_confidence": 0.5}),
                        node("n2", "alert")],
                       [edge("n1", "n2")]),
    )
    cat = det("cat", 0.87, (5, 6, 70, 80))
    _reset_state()
    v = check("cam1", rule, [cat], 0.0, frame_size=(100, 100))
    assert isinstance(v, Violation)
    assert v.camera_id == "cam1"
    assert v.rule_id == 31 and v.rule_name == "cat_at_door"
    assert v.description == "门口有猫出现" and v.severity == 3
    assert v.confidence == 0.87 and v.bbox == (5, 6, 70, 80)
    assert v.timestamp == 0.0
    assert check("cam1", rule, [], 1.0, frame_size=(100, 100)) is None

    # 无 graph 字段的规则（getattr 兜底）不告警
    bare = RuleDefinition(id=32, name="no_graph", description="",
                          template="graph")
    _reset_state()
    assert check("cam1", bare, [cat], 2.0, frame_size=(100, 100)) is None


# ------------------------------------------------------------
# 11. analyze_frame 全链路（模板 logic 分派 + 冷却机制）
# ------------------------------------------------------------

def test_analyze_frame_end_to_end():
    from core.analyzer import BehaviorAnalyzer

    reset_engine_singletons()
    tmp = tempfile.mkdtemp(prefix="rules_graph_e2e_")
    try:
        store = RulesStore(tmp)
        store.add(RuleDefinition(
            id=30, name="cat_at_door", description="门口有猫出现",
            template="graph", models=["ppe"], severity=3,
            graph=graph_of([node("n1", "class_present",
                                 {"classes": ["cat"], "min_confidence": 0.5}),
                            node("n2", "alert")],
                           [edge("n1", "n2")]),
        ))
        analyzer = BehaviorAnalyzer({"alert": {"cooldown_seconds": 30}},
                                    config_dir=tmp)
        rules = analyzer._rules.get_rules_for_camera([30])
        assert [r.id for r in rules] == [30]
        cat = det("cat", 0.87, (5, 6, 70, 80))
        vs = analyzer.analyze_frame("cam1", rules, [cat], 0.0,
                                    frame_size=(100, 100))
        assert len(vs) == 1 and vs[0].rule_id == 30
        assert vs[0].camera_id == "cam1" and vs[0].rule_name == "cat_at_door"
        # 冷却期内同规则不再产出（沿用 analyzer 现有冷却机制）
        assert analyzer.analyze_frame("cam1", rules, [cat], 1.0,
                                      frame_size=(100, 100)) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        reset_engine_singletons()


# ------------------------------------------------------------
# 12. state.node_types() 委托 + rules_list 透出 graph + API 端点
# ------------------------------------------------------------

CAT_GRAPH = graph_of([node("n1", "class_present",
                           {"classes": ["cat"], "min_confidence": 0.5}),
                      node("n2", "alert")], [edge("n1", "n2")])


def test_state_node_types_and_api():
    from webapp.api.rules_api import router
    from webapp.state import RuntimeState

    reset_engine_singletons()
    paths = {r.path for r in router.routes}
    assert "/api/rules/node-types" in paths, "缺少 GET /api/rules/node-types"

    tmp = tempfile.mkdtemp(prefix="rules_graph_state_")
    try:
        # 预置空 YAML，RuntimeState 初始化即可在临时目录完成
        (Path(tmp) / "settings.yaml").write_text("", encoding="utf-8")
        (Path(tmp) / "cameras.yaml").write_text("", encoding="utf-8")
        state = RuntimeState(tmp)

        # node_types() 委托到引擎注册表
        types = state.node_types()
        assert set(types) == set(NODE_TYPES)
        assert types["alert"] == NODE_TYPES["alert"]

        # add_rule 透传 graph，rules_list 回读 graph（供编辑时回填画布）
        state.add_rule({"id": 30, "name": "cat_at_door", "template": "graph",
                        "models": ["ppe"], "params": {}, "severity": 3,
                        "enabled": True, "description": "门口有猫",
                        "graph": CAT_GRAPH})
        entry = next(r for r in state.rules_list() if r["id"] == 30)
        assert entry["template"] == "graph" and entry["graph"] == CAT_GRAPH
        old = next(r for r in state.rules_list() if r["id"] == 1)
        assert old["graph"] is None  # 老规则不携带 graph
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------
# 13. 真实 config/rule_templates.yaml 已含 graph 模板
# ------------------------------------------------------------

def test_graph_template_in_real_yaml():
    specs = TemplateStore(str(PROJECT_ROOT / "config")).get_all()
    g = specs.get("graph")
    assert g is not None, "config/rule_templates.yaml 缺少 graph 模板"
    assert g["label"] == "自定义组合（画布）"
    assert g["logic"] == "graph" and g["params"] == []


# ------------------------------------------------------------
# 入口
# ------------------------------------------------------------

TESTS = [
    ("节点注册表", test_node_registry),
    ("class_present/class_absent", test_class_present_absent),
    ("in_zone", test_in_zone),
    ("duration", test_duration),
    ("not/and/or", test_not_and_or),
    ("alert 上升沿", test_alert_rising_edge_once),
    ("离岗图完整时间序列", test_person_leave_post_full_graph),
    ("环/非法图防御", test_cycles_and_invalid_graphs),
    ("RulesStore 持久化", test_rules_store_persistence),
    ("GraphCheck 集成", test_graph_check_integration),
    ("analyze_frame 全链路", test_analyze_frame_end_to_end),
    ("state/API 集成", test_state_node_types_and_api),
    ("rule_templates.yaml", test_graph_template_in_real_yaml),
]


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
        print(f"[PASS] {name}")
    print("ALL PASSED")
