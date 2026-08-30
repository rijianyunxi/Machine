/* 存量规则 ↔ 节点画布 的等价转换层：
 * - ruleToGraph: 老模板参数 → 等价节点图（编辑/展示用）
 * - graphToParams: 画布结构仍匹配该模板的规范图时 → 回写参数（检测行为零变化）
 *   结构被改动（加/删/重连节点）或调了模板不支持的新参数（如 ppe 的置信度）
 *   → 返回 null，调用方转存为独立图规则（template=graph）。
 *
 * ppe_absence 的等价图带分叉（节点 id 固定，供回写时精确匹配）：
 *   n1(person在场) ─────────┐
 *   ng(装备覆盖) → nn(非) ──┤→ no(或) → na(且) → al(告警)
 *   nv(一票否决类) ─────────┘ */

import type { RuleGraph } from "../api/types";

export const CONVERTIBLE_TEMPLATES: Record<string, string> = {
  generic_presence: "出现即告警",
  presence: "出现即告警",
  presence_near_person: "靠近人员才告警",
  zone_intrusion: "区域侵入告警",
  ppe_absence: "装备缺失检查",
};

type Params = Record<string, unknown>;
type N = { id: string; type: string; params: Params };

/** 从 alert 沿唯一父节点回溯的链路类型序列；分叉/环返回 null */
function chainTypes(g: RuleGraph): string[] | null {
  const alert = g.nodes.find((n) => n.type === "alert");
  if (!alert) return null;
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  for (const e of g.edges) {
    parents.set(e.to, [...(parents.get(e.to) || []), e.from]);
  }
  const chain: N[] = [];
  let cur: N | undefined = alert;
  const seen = new Set<string>();
  while (cur) {
    if (seen.has(cur.id)) return null;
    seen.add(cur.id);
    chain.unshift(cur);
    const ps = parents.get(cur.id) || [];
    if (ps.length === 0) break;
    if (ps.length > 1) return null;
    cur = byId.get(ps[0]);
  }
  return chain.map((n) => n.type);
}

const findNode = (g: RuleGraph, id: string) =>
  g.nodes.find((n) => n.id === id);

/** 老模板参数 → 等价节点图；不可转换返回 null */
export function ruleToGraph(template: string, params: Params): RuleGraph | null {
  const p = params || {};
  const chainEdges = (ids: string[]) =>
    ids.slice(1).map((id, i) => ({ from: ids[i], to: id }));

  if (template === "generic_presence" || template === "presence") {
    return { nodes: [
      { id: "n1", type: "class_present", params: {
          classes: (p.trigger_classes as string[]) || [],
          min_confidence: Number(p.min_confidence ?? 0.5) } },
      { id: "n2", type: "alert", params: {} },
    ], edges: chainEdges(["n1", "n2"]) };
  }
  if (template === "presence_near_person") {
    return { nodes: [
      { id: "n1", type: "class_present", params: {
          classes: (p.trigger_classes as string[]) || [],
          min_confidence: Number(p.min_confidence ?? 0) } },
      { id: "n2", type: "near_class", params: {
          ref_classes: (p.person_classes as string[]) || ["person"],
          margin: Number(p.overlap_margin ?? 0.2) } },
      { id: "n3", type: "alert", params: {} },
    ], edges: chainEdges(["n1", "n2", "n3"]) };
  }
  if (template === "zone_intrusion") {
    const dwell = Number(p.dwell_seconds ?? 0);
    const ids = ["n1", "n2", ...(dwell > 0 ? ["n3"] : []), "nx"];
    return { nodes: [
      { id: "n1", type: "class_present", params: {
          classes: (p.target_classes as string[]) || [],
          min_confidence: Number(p.min_confidence ?? 0.5) } },
      { id: "n2", type: "in_zone", params: {
          zones: (p.zones as Array<Record<string, number>>) || [] } },
      ...(dwell > 0
        ? [{ id: "n3", type: "duration", params: { seconds: dwell } }]
        : []),
      { id: "nx", type: "alert", params: {} },
    ], edges: chainEdges(ids) };
  }
  if (template === "ppe_absence") {
    // person在场 且 (检出no-hardhat一票否决 或 未(hardhat覆盖person)) → 告警
    // 置信度固定 0（原判定无置信度过滤，画布上调 → 转存为 graph 规则保留调参）
    return { nodes: [
      { id: "n1", type: "class_present", params: {
          classes: (p.person_classes as string[]) || ["person"],
          min_confidence: 0 } },
      { id: "ng", type: "class_covering", params: {
          classes: (p.required_classes as string[]) || [],
          ref_classes: (p.person_classes as string[]) || ["person"],
          coverage_ratio: Number(p.coverage_ratio ?? 0.5),
          min_confidence: 0 } },
      { id: "nn", type: "not", params: {} },
      { id: "nv", type: "class_present", params: {
          classes: (p.absence_classes as string[]) || [],
          min_confidence: 0 } },
      { id: "no", type: "or", params: {} },
      { id: "na", type: "and", params: {} },
      { id: "al", type: "alert", params: {} },
    ], edges: chainEdges(["ng", "nn", "no", "na", "al"]).concat([
      { from: "n1", to: "na" },
    ]) };
  }
  return null;
}

/** 画布结构仍匹配模板规范图时，从节点读回参数；否则 null */
export function graphToParams(
  template: string,
  graph: RuleGraph,
): Params | null {
  const p = (type: string) =>
    findNode(graph, type)?.params as Params | undefined;

  if (template === "generic_presence" || template === "presence") {
    if (chainTypes(graph)?.join() !== "class_present,alert") return null;
    const cp = p("class_present") || {};
    return {
      trigger_classes: cp.classes || [],
      min_confidence: Number(cp.min_confidence ?? 0.5),
    };
  }
  if (template === "presence_near_person") {
    if (chainTypes(graph)?.join() !== "class_present,near_class,alert") return null;
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
    const types = chainTypes(graph)?.join();
    const withDur = types === "class_present,in_zone,duration,alert";
    const withoutDur = types === "class_present,in_zone,alert";
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
  if (template === "ppe_absence") {
    // ruleToGraph 生成的节点 id 固定：精确匹配 id+类型+边集合，
    // 完全一致才回写（结构或置信度被改 → 返回 null 转存为 graph 规则）
    const want: Record<string, string> = {
      n1: "class_present", ng: "class_covering", nn: "not",
      nv: "class_present", no: "or", na: "and", al: "alert",
    };
    if (graph.nodes.length !== 7) return null;
    for (const n of graph.nodes) {
      if (want[n.id] !== n.type) return null;
    }
    const edges = graph.edges.map((e) => `${e.from}->${e.to}`).sort().join();
    const canonical = ["ng->nn", "nn->no", "nv->no", "no->na", "n1->na", "na->al"]
      .sort().join();
    if (edges !== canonical) return null;
    const gp = p("class_covering") || {};
    const n1 = p("class_present") || {}; // 同类型两节点，按固定 id 取参
    const n1Params = findNode(graph, "n1")?.params as Params | undefined;
    const nvParams = findNode(graph, "nv")?.params as Params | undefined;
    // 原模板无置信度过滤：画布上调过置信度 → 转存为 graph 规则以保留调参
    if (!n1Params || !nvParams) return null;
    if (Number(gp.min_confidence ?? 0) !== 0) return null;
    if (Number(n1Params.min_confidence ?? 0) !== 0) return null;
    if (Number(nvParams.min_confidence ?? 0) !== 0) return null;
    return {
      person_classes: n1Params.classes || ["person"],
      required_classes: gp.classes || [],
      absence_classes: nvParams.classes || [],
      coverage_ratio: Number(gp.coverage_ratio ?? 0.5),
    };
  }
  return null;
}
