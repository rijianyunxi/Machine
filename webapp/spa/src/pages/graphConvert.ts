/* 存量规则 ↔ 节点画布 的等价转换层：
 * - ruleToGraph: 老模板参数 → 等价节点图（编辑/展示用）
 * - graphToParams: 画布结构仍匹配该模板的规范链路时 → 回写参数（检测行为零变化）
 *   结构被改动（加/删/重连节点）→ 返回 null，调用方转存为独立图规则（template=graph）。
 * ppe_absence 是专用判定（一票否决 + 覆盖率），不在可转换集合，保持原表单。 */

import type { RuleGraph } from "../api/types";

export const CONVERTIBLE_TEMPLATES: Record<string, string> = {
  generic_presence: "出现即告警",
  presence: "出现即告警",
  presence_near_person: "靠近人员才告警",
  zone_intrusion: "区域侵入告警",
};

type Params = Record<string, unknown>;
type N = { id: string; type: string; params: Params };

const chainTypes = (g: RuleGraph): string[] | null => {
  const alert = g.nodes.find((n) => n.type === "alert");
  if (!alert) return null;
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  for (const e of g.edges) {
    parents.set(e.to, [...(parents.get(e.to) || []), e.from]);
  }
  // 从 alert 沿唯一父节点回溯到源头，得到链路（含 alert）
  const chain: N[] = [];
  let cur: N | undefined = alert;
  const seen = new Set<string>();
  while (cur) {
    if (seen.has(cur.id)) return null; // 环
    seen.add(cur.id);
    chain.unshift(cur);
    const ps = parents.get(cur.id) || [];
    if (ps.length === 0) break;
    if (ps.length > 1) return null; // 分叉：不属于线性链路
    cur = byId.get(ps[0]);
  }
  return chain.map((n) => n.type);
};

const findNode = (g: RuleGraph, type: string) =>
  g.nodes.find((n) => n.type === type);

/** 老模板参数 → 等价节点图；不可转换返回 null */
export function ruleToGraph(template: string, params: Params): RuleGraph | null {
  const p = params || {};
  let nodes: N[];
  if (template === "generic_presence" || template === "presence") {
    nodes = [
      { id: "n1", type: "class_present", params: {
          classes: (p.trigger_classes as string[]) || [],
          min_confidence: Number(p.min_confidence ?? 0.5) } },
      { id: "n2", type: "alert", params: {} },
    ];
    return { nodes, edges: [{ from: "n1", to: "n2" }] };
  }
  if (template === "presence_near_person") {
    nodes = [
      { id: "n1", type: "class_present", params: {
          classes: (p.trigger_classes as string[]) || [],
          min_confidence: Number(p.min_confidence ?? 0) } },
      { id: "n2", type: "near_class", params: {
          ref_classes: (p.person_classes as string[]) || ["person"],
          margin: Number(p.overlap_margin ?? 0.2) } },
      { id: "n3", type: "alert", params: {} },
    ];
    return { nodes, edges: [{ from: "n1", to: "n2" }, { from: "n2", to: "n3" }] };
  }
  if (template === "zone_intrusion") {
    const dwell = Number(p.dwell_seconds ?? 0);
    nodes = [
      { id: "n1", type: "class_present", params: {
          classes: (p.target_classes as string[]) || [],
          min_confidence: Number(p.min_confidence ?? 0.5) } },
      { id: "n2", type: "in_zone", params: {
          zones: (p.zones as Array<Record<string, number>>) || [] } },
      ...(dwell > 0
        ? [{ id: "n3", type: "duration", params: { seconds: dwell } }]
        : []),
      { id: "nx", type: "alert", params: {} },
    ];
    const edges: Array<{ from: string; to: string }> = [];
    for (let i = 0; i < nodes.length - 1; i++)
      edges.push({ from: nodes[i].id, to: nodes[i + 1].id });
    return { nodes, edges };
  }
  return null; // ppe_absence 等专用判定不做转换
}

/** 画布结构仍匹配模板规范链路时，从节点读回参数；否则 null */
export function graphToParams(
  template: string,
  graph: RuleGraph,
): Params | null {
  const types = chainTypes(graph);
  if (!types || types[types.length - 1] !== "alert") return null;
  const p = (type: string) =>
    findNode(graph, type)?.params as Params | undefined;

  if (template === "generic_presence" || template === "presence") {
    if (chainTypes(graph)?.join() !== "class_present,alert") return null;
    const cp = p("class_present") || {};
    return { trigger_classes: cp.classes || [], min_confidence: Number(cp.min_confidence ?? 0.5) };
  }
  if (template === "presence_near_person") {
    if (types.join() !== "class_present,near_class,alert") return null;
    const cp = p("class_present") || {};
    const nc = p("near_class") || {};
    return {
      trigger_classes: cp.classes || [],
      min_confidence: Number(cp.min_confidence ?? 0),
      person_classes: nc.ref_classes || ["person"],
      overlap_margin: Number(nc.margin ?? 0.2),
    };
  }
  if (template === "zone_intrusion") {
    const withDur = types.join() === "class_present,in_zone,duration,alert";
    const withoutDur = types.join() === "class_present,in_zone,alert";
    if (!withDur && !withoutDur) return null;
    const cp = p("class_present") || {};
    const z = p("in_zone") || {};
    const d = p("duration") || {};
    return {
      target_classes: cp.classes || [],
      min_confidence: Number(cp.min_confidence ?? 0.5),
      zones: z.zones || [],
      dwell_seconds: withDur ? Number(d.seconds ?? 0) : 0,
    };
  }
  return null;
}
