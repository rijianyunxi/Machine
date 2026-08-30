# 可视化规则画布 · 开发契约（v1）

> 本文档是前后端两个开发任务的唯一契约。实现必须与本文档完全一致；
> 有偏差宁可停下来在交付报告里说明，不要自行发明接口。

## 1. 目标

规则从"固定判定逻辑"升级为"节点图"：客户在画布上拖拽积木、连线组成检测逻辑。
第一期交付：数据模型 + 通用图执行引擎（后端）+ 画布编辑器与预设画廊（前端）+
8 个预置场景，存量规则完全兼容。

## 2. 数据模型（rules.yaml 条目）

在现有规则条目上新增可选 `graph` 字段；`template` 固定写 `graph`：

```yaml
- id: 30
  name: person_leave_post
  description: 值守区域人员离开
  template: graph          # 固定值
  models: [ppe]
  severity: 2
  enabled: true
  params: {}
  graph:
    nodes:
      - {id: n1, type: class_present, params: {classes: [person], min_confidence: 0.5}}
      - {id: n2, type: not}
      - {id: n3, type: duration, params: {seconds: 10}}
      - {id: n4, type: alert, params: {}}
    edges:
      - {from: n1, to: n2}
      - {from: n2, to: n3}
      - {from: n3, to: n4}
```

存量规则（template=ppe_absence 等）不受影响、不迁移。

## 3. 信号与求值语义

- 信号对象：`{state: bool, targets: list[Detection]}`。targets 是该信号所指的
  检测框（像素坐标），供空间节点做几何过滤。
- 每帧对图做一次**拓扑序**求值；有环的图整图跳过（日志 warning，不崩溃）。
- `alert` 节点在输入信号**上升沿**（上一帧 false → 本帧 true）时产生一次 Violation；
  冷却时间沿用 analyzer 现有机制。duration/在区状态等按 (rule_id, node_id) 记忆。

## 4. 节点注册表（v1 共 8 种）

| type | 中文名 | 分类 | 输入数 | 参数 schema |
|------|--------|------|--------|-------------|
| class_present | 类别在场 | 目标 | 0 | classes: string[]（必填），min_confidence: float=0.5 |
| class_absent  | 类别离场 | 目标 | 0 | classes: string[]（必填），min_confidence: float=0.5 |
| in_zone       | 在指定区域内 | 空间 | 1 | zones: [{x,y,w,h}]（归一化，左上角原点） |
| duration      | 持续N秒 | 时间 | 1 | seconds: float=10（≥0） |
| not           | 非 | 逻辑 | 1 | 无 |
| and           | 且 | 逻辑 | 2 | 无 |
| or            | 或 | 逻辑 | 2 | 无 |
| alert         | 告警 | 输出 | 1 | 无（严重度用规则级 severity） |

语义细则：
- `class_present`：任一 classes 检出且 conf≥min → state=true，targets=该类检测框；否则 state=false、targets=[]。
- `class_absent`：classes 全部不在场 → state=true，targets=[]。
- `in_zone`：输入 targets 的**中心点**落在任一 zone 内 → state=true，targets=过滤后子集；输入无 targets 时 state=false。
- `duration`：对输入 state 做"连续为真计时"；连续 ≥ seconds → 输出 true（targets 原样透传）；中断即清零。
- `not`：state 取反，targets 清空。
- `and`/`or`：对两个输入的 state 取与/或；targets 取第一个有 targets 的输入。
- `alert`：消耗信号，不产生输出。

## 5. 后端集成点

- 新文件 `core/rules_graph.py`：节点注册表 `NODE_TYPES`、图求值 `evaluate_graph(graph, detections, frame_size, timestamp, rule_id)`（返回 violations 或 None）。
- `rules_engine`：`rule_templates.yaml` 增加模板 `graph`（label "自定义组合（画布）"，logic `graph`，params `[]`）；
  analyzer 的 `RULE_LOGICS` 注册 `graph → GraphCheck`，`GraphCheck` 从 `rule.graph` 读图求值
  （graph 缺失/为空/非法 → 不告警）。`analyze_frame` 已有 frame_size 参数，透传给求值器。
- **API**：`webapp/api/rules_api.py` 新增 `GET /api/rules/node-types`，
  返回 `{"node_types": {type: {label, category, inputs, outputs, params: [{name,type,default,desc,min,max}]}}}`
  （params schema 供前端自动生成表单）。`webapp/state.py` 加 `node_types()` 委托到引擎注册表。
- 规则新增/编辑走现有 POST/PUT /api/rules（body 里带 graph 字段），RulesStore 需持久化 graph 字段。

## 6. 前端集成点

- 新文件 `webapp/spa/src/ui/GraphEditor.tsx`：画布编辑器组件。
  props：`{graph, onChange, nodeTypes, classOptions, cameras}`。
  - 左侧积木库（按 category 分组，来自 nodeTypes，中文 label）
  - 画布：节点可拖拽定位（前端布局坐标存于节点 params 之外的 UI 状态即可，**不入库也不强求**），
    SVG 连线（从源节点右侧输出点 → 目标节点左侧输入点），点击连线可删除
  - 右侧参数面板：选中节点的 params 表单，**按 params schema 自动生成**
    （string[] + from 类目 → 复用类别 tag 点选；zones → 复用 ZoneRectEditor；float/int → 数字框）
  - 校验：必须恰好含一个 alert 节点、无环、alert 必须是汇点；不满足时禁用保存并提示
- 新文件 `webapp/spa/src/pages/graphPresets.ts`：8 个预置画布（见 §7）+ 空白画布。
- `Rules.tsx` 集成：
  - 「＋新建规则」先弹**预设画廊**（8 场景卡片 + 空白画布卡片），选中后进入规则编辑
    （判定逻辑区域整体替换为 GraphEditor，名称/严重度/绑定模型/启用等表单保留）
  - 保存 body：`{template: "graph", graph, name, models, severity, enabled, description, params: {}}`
  - 规则列表卡片：`template === "graph"` 的规则显示 label「自定义组合」；
    编辑时直接打开画布编辑器（不经过画廊）
  - **判定逻辑下拉不再出现**：新建永远走画廊/画布；编辑存量老规则时保持原有"何时告警"表单

## 7. 八个预置画布（graphPresets.ts）

| key | 标题 | 图 |
|-----|------|-----|
| cat_at_door | 门口有猫（出现即告警） | class_present[cat] → alert |
| person_leave | 人员离开画面（离岗） | class_present[person] → not → duration[10s] → alert |
| person_near_door | 有人靠近门口 | class_present[person] → in_zone[门口区域] → alert |
| fence_intrusion | 闯入围墙 | class_present[person] → in_zone[围墙区域] → alert |
| danger_dwell | 危险区域逗留 | class_present[person] → in_zone[危险区域] → duration[30s] → alert |
| zone_cleared | 区域清空/物品被盗 | class_present[包裹类目] → not → duration[10s] → alert |
| smoking | 吸烟 | class_present[cigarette] → near_person → alert |
| no_helmet | 未戴安全帽 | class_present[person] → and → alert；class_absent/hardhat-on-person 分支 |

说明：smoking/no_helmet 需要"靠近参照类别"能力。v1 节点表若不含 near 节点，
这两个预设允许**近似实现**（如 smoking 用 class_present[cigarette]→alert，
no_helmet 用 class_present[person]→alert），并在预设 desc 里注明"简化版"。
禁止为此私自扩节点类型——**near/overlap 节点列入二期**，由总指挥决定。

## 8. 约束

- 禁止引入任何新依赖（前端不加 npm 包，后端不加 pip 包）
- 前端 TypeScript strict 通过 `npm run build`；后端 `py_compile` + 自测脚本全绿
- Agent-A 不得改 webapp/spa/**；Agent-B 不得改 webapp/spa 以外的任何文件
- 双方都不执行 git commit（由总指挥验收后统一提交）
