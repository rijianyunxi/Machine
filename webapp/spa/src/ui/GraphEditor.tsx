import { useEffect, useMemo, useRef, useState } from "react";
import type {
  GraphEdge,
  GraphNode,
  GraphNodeTypeSpec,
  RuleGraph,
} from "../api/types";
import { ZoneRectEditor } from "./ZoneRectEditor";

/* 可视化规则画布编辑器（契约 docs/RULE_GRAPH_DESIGN.md §6）。
 * 左侧积木库（按分类分组）/ 中部画布（节点卡片 + SVG 连线）/ 右侧参数面板。
 * 节点画布坐标仅存运行时 state，不写入 graph 数据。 */

export interface GraphEditorProps {
  graph: RuleGraph;
  onChange: (g: RuleGraph) => void;
  nodeTypes: Record<string, GraphNodeTypeSpec>;
  classOptions: string[];
  cameras: { id: string; name: string }[];
  /** 节点库（GET /api/rules/node-types）加载失败标记 */
  loadError?: boolean;
}

interface Pt {
  x: number;
  y: number;
}

const NODE_W = 180;
const PORT_Y = 24;
const CANVAS_W = 1500;
const CANVAS_H = 1000;
const COL_STEP = 216;
const PAD = 24;

const CAT_ORDER = ["目标", "空间", "时间", "逻辑", "输出"];
const CAT_COLOR: Record<string, string> = {
  目标: "var(--accent)",
  空间: "var(--yellow)",
  时间: "var(--green)",
  逻辑: "var(--muted)",
  输出: "var(--red)",
};
const catColor = (c: string) => CAT_COLOR[c] || "var(--muted)";

type Sel = { kind: "node"; id: string } | { kind: "edge"; key: string } | null;
const edgeKey = (e: GraphEdge) => `${e.from}>${e.to}`;

/* 画布校验（契约 §6）：恰好一个 alert、无环（DFS 三色标记）、alert 无输出边 */
export function validateGraph(
  graph: RuleGraph,
  nodeTypes: Record<string, GraphNodeTypeSpec>,
): string[] {
  const errs: string[] = [];
  const ids = new Set(graph.nodes.map((n) => n.id));
  const alerts = graph.nodes.filter((n) => n.type === "alert");
  if (alerts.length === 0) errs.push("画布缺少「告警」节点：请从积木库添加一个告警节点");
  else if (alerts.length > 1)
    errs.push(`「告警」节点只能有 1 个（当前 ${alerts.length} 个）`);
  if (alerts.length && graph.edges.some((e) => e.from === alerts[0].id && ids.has(e.to)))
    errs.push("「告警」是输出节点，不能再连出到其他节点");

  /* 环检测 */
  const adj = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (!ids.has(e.from) || !ids.has(e.to)) continue;
    adj.set(e.from, [...(adj.get(e.from) || []), e.to]);
  }
  const color = new Map<string, 0 | 1 | 2>();
  let hasCycle = false;
  const dfs = (u: string) => {
    color.set(u, 1);
    for (const v of adj.get(u) || []) {
      if (hasCycle) return;
      const c = color.get(v) ?? 0;
      if (c === 1) hasCycle = true;
      else if (c === 0) dfs(v);
    }
    color.set(u, 2);
  };
  for (const n of graph.nodes) {
    if (hasCycle) break;
    if ((color.get(n.id) ?? 0) === 0) dfs(n.id);
  }
  if (hasCycle) errs.push("图中存在循环连线，请删除构成环的边");
  if (graph.edges.some((e) => !ids.has(e.from) || !ids.has(e.to)))
    errs.push("存在指向已删除节点的连线，请点击该连线删除");

  /* 安全兜底：类别节点 classes 为空会导致永不触发/常触发 */
  for (const n of graph.nodes) {
    if (n.type === "class_present" || n.type === "class_absent") {
      const cs = n.params?.classes;
      if (!Array.isArray(cs) || !cs.length)
        errs.push(
          `节点 ${n.id}（${nodeTypes[n.type]?.label || n.type}）至少要选择一个类别`,
        );
    }
  }
  return errs;
}

/* 节点默认参数（按 schema 填默认值）；类别参数兼容 "string[]" / "classes" 两种写法 */
function defaultParams(spec: GraphNodeTypeSpec | undefined): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const p of spec?.params || []) {
    if (p.type === "classes" || p.type === "string[]" || p.type === "zones")
      params[p.name] = Array.isArray(p.default) ? p.default : [];
    else if (p.default !== undefined && p.default !== null) params[p.name] = p.default;
    else if (p.type === "float" || p.type === "int") params[p.name] = 0;
    else params[p.name] = "";
  }
  return params;
}

function nextNodeId(nodes: GraphNode[]): string {
  let max = 0;
  for (const n of nodes) {
    const m = /^n(\d+)$/.exec(n.id);
    if (m) max = Math.max(max, +m[1]);
  }
  return `n${max + 1}`;
}

/* 拓扑深度（松弛迭代，环安全），用于新节点落位 */
function nodeDepths(graph: RuleGraph): Record<string, number> {
  const preds = new Map<string, string[]>();
  for (const e of graph.edges) preds.set(e.to, [...(preds.get(e.to) || []), e.from]);
  const d: Record<string, number> = {};
  for (let i = 0; i < graph.nodes.length + 1; i++) {
    for (const n of graph.nodes) {
      const ps = preds.get(n.id) || [];
      if (!ps.length) {
        d[n.id] = Math.max(d[n.id] ?? 0, 0);
        continue;
      }
      const max = Math.max(...ps.map((p) => d[p] ?? -1));
      if (max + 1 > (d[n.id] ?? -1)) d[n.id] = max + 1;
    }
  }
  return d;
}

function edgePath(a: Pt, b: Pt): string {
  const dx = Math.max(36, Math.abs(b.x - a.x) / 2);
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
}

function edgeMid(a: Pt, b: Pt): Pt {
  const dx = Math.max(36, Math.abs(b.x - a.x) / 2);
  const c1 = { x: a.x + dx, y: a.y };
  const c2 = { x: b.x - dx, y: b.y };
  return {
    x: 0.125 * a.x + 0.375 * c1.x + 0.375 * c2.x + 0.125 * b.x,
    y: 0.125 * a.y + 0.375 * c1.y + 0.375 * c2.y + 0.125 * b.y,
  };
}

/* 节点卡片第二行：参数摘要 */
function summarize(node: GraphNode, spec: GraphNodeTypeSpec | undefined): string {
  const parts: string[] = [];
  for (const p of spec?.params || []) {
    const v = node.params?.[p.name];
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) {
      if (!v.length) continue;
      parts.push(
        v.every((x) => typeof x === "string")
          ? (v as string[]).join("、")
          : `${v.length} 个区域`,
      );
    } else if (p.name === "seconds") parts.push(`${v} 秒`);
    else if (p.name === "min_confidence") parts.push(`置信度≥${v}`);
    else parts.push(String(v));
  }
  return parts.join(" · ") || "未配置参数";
}

/* 类别参数：chips 点选 + 手输（复用 .tag-input 样式） */
function ClassChips({
  value,
  options,
  onChange,
}: {
  value: string[];
  options: string[];
  onChange: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    if (!value.some((x) => x.toLowerCase() === v.toLowerCase()))
      onChange([...value, v]);
  };
  const fresh = options.filter(
    (o) => !value.some((v) => v.toLowerCase() === o.toLowerCase()),
  );
  return (
    <div className="tag-input">
      {value.length ? (
        <div className="tag-rows">
          {value.map((v) => (
            <span key={v} className="chip blue tag-x">
              {v}
              <button
                type="button"
                title="移除"
                onClick={() => onChange(value.filter((x) => x !== v))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      {fresh.length ? (
        <div className="tag-sugs">
          {fresh.slice(0, 8).map((o) => (
            <button key={o} type="button" className="mini ghost" onClick={() => add(o)}>
              + {o}
            </button>
          ))}
          {fresh.length > 8 ? (
            <span className="muted" style={{ fontSize: 11 }}>
              还有 {fresh.length - 8} 个…
            </span>
          ) : null}
        </div>
      ) : null}
      <input
        style={{ width: "100%" }}
        value={draft}
        placeholder="输入类别后回车添加"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            draft
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean)
              .forEach(add);
            setDraft("");
          }
        }}
        onBlur={() => setDraft("")}
      />
    </div>
  );
}

export function GraphEditor({
  graph,
  onChange,
  nodeTypes,
  classOptions,
  cameras,
  loadError,
}: GraphEditorProps) {
  const [pos, setPos] = useState<Record<string, Pt>>({});
  const [sel, setSel] = useState<Sel>(null);
  const [linking, setLinking] = useState<string | null>(null);
  const [mouse, setMouse] = useState<Pt | null>(null);
  const [hint, setHint] = useState("");

  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<null | { id: string; dx: number; dy: number }>(null);
  const justDragged = useRef(false);
  const linkingRef = useRef<string | null>(null);
  const hintTimer = useRef<number | null>(null);

  const specs = Object.values(nodeTypes);
  const emptyLib = loadError || specs.length === 0;

  const specOf = (type: string) => nodeTypes[type];
  const labelOf = (n: GraphNode) => nodeTypes[n.type]?.label || n.type;

  const flash = (m: string) => {
    setHint(m);
    if (hintTimer.current) window.clearTimeout(hintTimer.current);
    hintTimer.current = window.setTimeout(() => setHint(""), 2600);
  };

  /* 新节点自动落位：按拓扑深度分列（已定位节点保持不动） */
  useEffect(() => {
    setPos((prev) => {
      const missing = graph.nodes.filter((n) => !prev[n.id]);
      if (!missing.length) return prev;
      const depth = nodeDepths(graph);
      const colCount: Record<number, number> = {};
      for (const p of Object.values(prev)) {
        const c = Math.round((p.x - PAD) / COL_STEP);
        colCount[c] = (colCount[c] || 0) + 1;
      }
      const next = { ...prev };
      for (const n of missing) {
        const col = Math.min(depth[n.id] ?? 0, 6);
        next[n.id] = { x: PAD + col * COL_STEP, y: PAD + (colCount[col] || 0) * 92 };
        colCount[col] = (colCount[col] || 0) + 1;
      }
      return next;
    });
  }, [graph]);

  /* 拖拽节点 / 连线跟随：全程挂 document（对齐 ZoneRectEditor 做法） */
  useEffect(() => {
    const canvasPt = (e: MouseEvent): Pt => {
      const r = canvasRef.current?.getBoundingClientRect();
      return r ? { x: e.clientX - r.left, y: e.clientY - r.top } : { x: 0, y: 0 };
    };
    const onMove = (e: MouseEvent) => {
      if (dragRef.current) {
        const { id, dx, dy } = dragRef.current;
        const c = canvasPt(e);
        setPos((prev) =>
          prev[id]
            ? {
                ...prev,
                [id]: {
                  x: Math.min(Math.max(c.x - dx, 0), CANVAS_W - NODE_W),
                  y: Math.min(Math.max(c.y - dy, 0), CANVAS_H - 48),
                },
              }
            : prev,
        );
      } else if (linkingRef.current) {
        setMouse(canvasPt(e));
      }
    };
    const onUp = () => {
      if (dragRef.current) {
        justDragged.current = true;
        dragRef.current = null;
        /* click 事件若未落到画布空白处，下一轮宏任务兜底清除 */
        window.setTimeout(() => {
          justDragged.current = false;
        }, 0);
      }
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, []);

  const errs = useMemo(
    () => (emptyLib ? [] : validateGraph(graph, nodeTypes)),
    [graph, nodeTypes, emptyLib],
  );

  /* ---------- 分组积木库 ---------- */
  const groups = useMemo(() => {
    const g = new Map<string, { type: string; spec: GraphNodeTypeSpec }[]>();
    for (const [type, spec] of Object.entries(nodeTypes)) {
      g.set(spec.category, [...(g.get(spec.category) || []), { type, spec }]);
    }
    return [...g.entries()].sort((a, b) => {
      const ia = CAT_ORDER.indexOf(a[0]);
      const ib = CAT_ORDER.indexOf(b[0]);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
  }, [nodeTypes]);

  /* ---------- 画布操作 ---------- */
  const addNode = (type: string) => {
    const id = nextNodeId(graph.nodes);
    onChange({
      ...graph,
      nodes: [...graph.nodes, { id, type, params: defaultParams(nodeTypes[type]) }],
    });
    setSel({ kind: "node", id });
  };

  const setNodeParams = (id: string, params: Record<string, unknown>) =>
    onChange({
      ...graph,
      nodes: graph.nodes.map((n) => (n.id === id ? { ...n, params } : n)),
    });

  const delNode = (id: string) => {
    onChange({
      nodes: graph.nodes.filter((n) => n.id !== id),
      edges: graph.edges.filter((e) => e.from !== id && e.to !== id),
    });
    setSel(null);
  };

  const delEdge = (key: string) => {
    onChange({ ...graph, edges: graph.edges.filter((e) => edgeKey(e) !== key) });
    setSel(null);
  };

  const tryConnect = (to: string) => {
    const from = linkingRef.current;
    if (!from) return;
    if (from === to) {
      flash("不能连接到节点自身");
      return;
    }
    if (graph.edges.some((e) => e.from === from && e.to === to)) {
      flash("这条连线已存在");
      return;
    }
    const tSpec = nodeTypes[graph.nodes.find((n) => n.id === to)?.type || ""];
    if (tSpec && tSpec.inputs === 0) {
      flash("该节点没有输入点，不能作为连线目标");
      return;
    }
    if (tSpec && tSpec.inputs === 1 && graph.edges.some((e) => e.to === to)) {
      flash("该节点只有一个输入，已被占用");
      return;
    }
    onChange({ ...graph, edges: [...graph.edges, { from, to }] });
    setLinking(null);
    linkingRef.current = null;
    setMouse(null);
  };

  const startLink = (from: string) => {
    setLinking(from);
    linkingRef.current = from;
    setMouse(null);
    setSel(null);
  };

  const cancelLink = () => {
    setLinking(null);
    linkingRef.current = null;
    setMouse(null);
  };

  const onNodeMouseDown = (e: React.MouseEvent, n: GraphNode) => {
    e.stopPropagation();
    if (linking) {
      tryConnect(n.id);
      return;
    }
    setSel({ kind: "node", id: n.id });
    const r = canvasRef.current?.getBoundingClientRect();
    const p = pos[n.id] || { x: 0, y: 0 };
    dragRef.current = {
      id: n.id,
      dx: r ? e.clientX - r.left - p.x : 0,
      dy: r ? e.clientY - r.top - p.y : 0,
    };
  };

  const onCanvasClick = (e: React.MouseEvent) => {
    if (e.target !== canvasRef.current) return;
    if (justDragged.current) {
      justDragged.current = false;
      return;
    }
    if (linking) cancelLink();
    else setSel(null);
  };

  /* ---------- 几何 ---------- */
  const outPt = (id: string): Pt | null => {
    const p = pos[id];
    return p ? { x: p.x + NODE_W, y: p.y + PORT_Y } : null;
  };
  const inPt = (id: string): Pt | null => {
    const p = pos[id];
    return p ? { x: p.x, y: p.y + PORT_Y } : null;
  };

  const liveEdges = graph.edges.filter(
    (e) => pos[e.from] && pos[e.to] && graph.nodes.some((n) => n.id === e.from) && graph.nodes.some((n) => n.id === e.to),
  );
  const linkSrc = linking ? outPt(linking) : null;

  const selEdge =
    sel?.kind === "edge" ? graph.edges.find((e) => edgeKey(e) === sel.key) : undefined;

  /* ---------- 节点库失败 ---------- */
  if (emptyLib)
    return (
      <div className="graph-wrap">
        <div className="graph-editor is-err">
          节点库加载失败（GET /api/rules/node-types），请刷新页面重试；保存已禁用。
        </div>
      </div>
    );

  const selNode = sel?.kind === "node" ? graph.nodes.find((n) => n.id === sel.id) : undefined;
  const selNodeSpec = selNode ? nodeTypes[selNode.type] : undefined;

  return (
    <div className="graph-wrap">
      <div className="graph-editor">
        {/* 左：积木库 */}
        <aside className="graph-palette">
          <div className="graph-pal-title">节点积木库</div>
          {groups.map(([cat, items]) => (
            <div key={cat}>
              <h5>
                <span className="dot" style={{ background: catColor(cat) }} />
                {cat}
              </h5>
              {items.map(({ type, spec }) => (
                <button
                  key={type}
                  type="button"
                  className="graph-pal-item"
                  title={`点击添加「${spec.label}」节点`}
                  onClick={() => addNode(type)}
                >
                  {spec.label}
                </button>
              ))}
            </div>
          ))}
        </aside>

        {/* 中：画布 */}
        <div className="graph-stage">
          <div className="graph-topbar">
            {hint ? (
              <span className="tip">{hint}</span>
            ) : linking ? (
              <span className="tip linking">
                连线中：点击目标节点完成连线，点击空白处取消
              </span>
            ) : (
              <span className="tip">
                拖拽节点移动 · 点击输出点再点目标节点连线 · 点击连线可删除
              </span>
            )}
          </div>
          <div
            ref={canvasRef}
            className="graph-canvas"
            onClick={onCanvasClick}
            onMouseDown={() => {
              if (linking) cancelLink();
            }}
          >
            <svg className="graph-svg">
              <defs>
                <marker
                  id="rg-arrow"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6.5"
                  markerHeight="6.5"
                  orient="auto"
                >
                  <path d="M0 0L10 5L0 10z" fill="var(--muted)" />
                </marker>
                <marker
                  id="rg-arrow-on"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6.5"
                  markerHeight="6.5"
                  orient="auto"
                >
                  <path d="M0 0L10 5L0 10z" fill="var(--accent)" />
                </marker>
              </defs>
              {liveEdges.map((e) => {
                const a = outPt(e.from)!;
                const b = inPt(e.to)!;
                const on = sel?.kind === "edge" && sel.key === edgeKey(e);
                return (
                  <g key={edgeKey(e)}>
                    <path
                      className={"graph-edge" + (on ? " on" : "")}
                      d={edgePath(a, b)}
                      markerEnd={on ? "url(#rg-arrow-on)" : "url(#rg-arrow)"}
                    />
                    <path
                      className="graph-edge-hit"
                      d={edgePath(a, b)}
                      onClick={(ev) => {
                        ev.stopPropagation();
                        if (linking) return;
                        setSel(
                          on
                            ? null
                            : { kind: "edge", key: edgeKey(e) },
                        );
                      }}
                    />
                  </g>
                );
              })}
              {linkSrc ? (
                <path
                  className="graph-edge temp"
                  d={edgePath(linkSrc, mouse || { x: linkSrc.x + 60, y: linkSrc.y + 24 })}
                />
              ) : null}
            </svg>
            {graph.nodes.map((n) => {
              const p = pos[n.id];
              if (!p) return null;
              const spec = specOf(n.type);
              const on = sel?.kind === "node" && sel.id === n.id;
              return (
                <div
                  key={n.id}
                  className={"graph-node" + (on ? " on" : "")}
                  style={{ left: p.x, top: p.y }}
                  onMouseDown={(e) => onNodeMouseDown(e, n)}
                >
                  {spec && spec.inputs > 0 ? (
                    <span
                      className={"graph-port in" + (linking ? " hot" : "")}
                      title={linking ? "点击完成连线" : "输入点"}
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        if (linking) tryConnect(n.id);
                        else {
                          setSel({ kind: "node", id: n.id });
                          flash("连线请从源节点的输出点开始：点击右侧圆点");
                        }
                      }}
                    />
                  ) : null}
                  {/* alert 按契约消耗信号、不产生输出（后端注册表 outputs=1 亦不渲染输出口） */}
                  {spec && spec.outputs > 0 && n.type !== "alert" ? (
                    <span
                      className="graph-port out"
                      title="点击此处，再点目标节点完成连线"
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        startLink(n.id);
                      }}
                    />
                  ) : null}
                  <div className="nt">
                    <span className="dot" style={{ background: catColor(spec?.category || "") }} />
                    {labelOf(n)}
                  </div>
                  <div className="np">{summarize(n, spec)}</div>
                </div>
              );
            })}
            {selEdge && sel?.kind === "edge" && pos[selEdge.from] && pos[selEdge.to] ? (
              (() => {
                const m = edgeMid(outPt(selEdge.from)!, inPt(selEdge.to)!);
                return (
                  <button
                    type="button"
                    className="graph-edge-del"
                    style={{ left: m.x, top: m.y }}
                    title="删除这条连线"
                    onClick={() => delEdge(edgeKey(selEdge))}
                  >
                    ×
                  </button>
                );
              })()
            ) : null}
          </div>
        </div>

        {/* 右：参数面板 */}
        <aside className="graph-params">
          {selNode ? (
            <>
              <h4>
                {labelOf(selNode)}
                <span className="mono">{selNode.id}</span>
              </h4>
              {selNodeSpec?.params.length ? (
                selNodeSpec.params.map((p) => {
                  const val =
                    selNode.params[p.name] !== undefined
                      ? selNode.params[p.name]
                      : p.default;
                  if (p.type === "classes" || p.type === "string[]") {
                    const arr: string[] = Array.isArray(val) ? (val as string[]) : [];
                    return (
                      <div className="field" key={p.name}>
                        <label>{p.desc || p.name}</label>
                        <ClassChips
                          value={arr}
                          options={classOptions}
                          onChange={(v) =>
                            setNodeParams(selNode.id, { ...selNode.params, [p.name]: v })
                          }
                        />
                      </div>
                    );
                  }
                  if (p.type === "zones") {
                    const rects = Array.isArray(val)
                      ? (val as Array<Record<string, number>>)
                      : [];
                    return (
                      <div className="field" key={p.name}>
                        <label>{p.desc || p.name}</label>
                        <ZoneRectEditor
                          value={rects}
                          cameras={cameras}
                          onChange={(v) =>
                            setNodeParams(selNode.id, { ...selNode.params, [p.name]: v })
                          }
                        />
                      </div>
                    );
                  }
                  if (p.type === "float" || p.type === "int") {
                    return (
                      <div className="field" key={p.name}>
                        <label>{p.desc || p.name}</label>
                        <input
                          style={{ width: "100%" }}
                          type="number"
                          step={p.type === "int" ? "1" : "0.05"}
                          min={p.min}
                          max={p.max}
                          value={String(val ?? "")}
                          onChange={(e) =>
                            setNodeParams(selNode.id, {
                              ...selNode.params,
                              [p.name]: +e.target.value,
                            })
                          }
                        />
                      </div>
                    );
                  }
                  return (
                    <div className="field" key={p.name}>
                      <label>{p.desc || p.name}</label>
                      <input
                        style={{ width: "100%" }}
                        value={String(val ?? "")}
                        onChange={(e) =>
                          setNodeParams(selNode.id, {
                            ...selNode.params,
                            [p.name]: e.target.value,
                          })
                        }
                      />
                    </div>
                  );
                })
              ) : (
                <p className="tips">该节点没有可调参数。</p>
              )}
              <button
                type="button"
                className="mini danger"
                style={{ marginTop: 8 }}
                onClick={() => delNode(selNode.id)}
              >
                删除节点
              </button>
            </>
          ) : (
            <div className="tips">
              <p style={{ fontWeight: 600, color: "var(--text-2)" }}>使用说明</p>
              <p>· 点击左侧积木添加节点</p>
              <p>· 拖拽节点卡片调整位置</p>
              <p>· 点击节点右侧输出点，再点目标节点完成连线</p>
              <p>· 点击连线选中后可删除</p>
              <p>· 选中节点在此编辑参数</p>
              <p>· 画布需要恰好一个「告警」节点作为终点</p>
            </div>
          )}
        </aside>
      </div>
      {errs.length ? (
        <div className="graph-errs">
          {errs.map((m, i) => (
            <div key={i}>✕ {m}</div>
          ))}
        </div>
      ) : (
        <div className="graph-ok">✓ 画布校验通过，可保存</div>
      )}
    </div>
  );
}
