import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  GraphNodeTypeSpec,
  ModelsResponse,
  NodeTypesResponse,
  ParamSpec,
  RuleEntry,
  RuleGraph,
  TemplateSpec,
} from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Chip, Empty, useBusy } from "../ui/badges";
import { ZoneRectEditor } from "../ui/ZoneRectEditor";
import { GraphEditor, validateGraph } from "../ui/GraphEditor";
import { BLANK_PRESET } from "./graphPresets";
import { CONVERTIBLE_TEMPLATES, graphToParams, ruleToGraph } from "./graphConvert";

type ParamValues = Record<string, unknown>;

const graphModels = (graph: RuleGraph): string[] => [
  ...new Set(
    graph.nodes
      .map((node) => node.model?.trim())
      .filter((model): model is string => Boolean(model)),
  ),
];

const attachGraphModels = (graph: RuleGraph, models: string[]): RuleGraph => ({
  ...graph,
  nodes: graph.nodes.map((node) =>
    node.model || !models.length || !["class_present", "class_absent", "class_covering", "near_class"].includes(node.type)
      ? node
      : { ...node, model: models[0] },
  ),
});

/*
 * 规则卡片展示的是“检测类别”，不是业务 category。
 * 普通模板的类别在 rule.params（trigger_classes/person_classes/...）中，
 * 画布模板的类别在各检测节点 params.classes/ref_classes 中；
 * zones、模型名以及其它数组参数不能混入这里。
 */
const isClassParam = (name: string): boolean =>
  name === "classes" || name === "class_names" || name.endsWith("_classes");

function ruleClasses(rule: RuleEntry): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  const add = (value: unknown) => {
    if (!Array.isArray(value)) return;
    for (const item of value) {
      if (typeof item !== "string") continue;
      const cls = item.trim();
      if (!cls) continue;
      const key = cls.toLocaleLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      result.push(cls);
    }
  };

  for (const [name, value] of Object.entries(rule.params || {})) {
    if (isClassParam(name)) add(value);
  }
  for (const node of rule.graph?.nodes || []) {
    for (const [name, value] of Object.entries(node.params || {})) {
      if (isClassParam(name)) add(value);
    }
  }
  return result;
}

/* 判定逻辑 -> 规则落盘时使用的内部模板名（模板已从界面隐藏，仅作存储机制） */
const LOGIC_CANONICAL: Record<string, string> = {
  presence: "generic_presence",
  presence_near: "presence_near_person",
  absence_required: "ppe_absence",
  zone_intrusion: "zone_intrusion",
};

interface RuleForm {
  id: string;
  name: string;
  description: string;
  logic: string;
  models: string[];
  params: ParamValues;
  severity: string;
  enabled: boolean;
  /* template === "graph"：画布规则（判定逻辑区域整体替换为 GraphEditor） */
  template: string;
  graph: RuleGraph;
}

/* 类别 tag 输入：已选为可删除的 chip；下方是模型类别建议（点选即加，
 * 且自动绑定来源模型）；也支持手输任意类别（回车添加）。 */
function ClassTagsInput({
  value,
  suggestions,
  placeholder,
  onChange,
  onPickSource,
}: {
  value: string[];
  suggestions: { cls: string; models: string[] }[];
  placeholder?: string;
  onChange: (v: string[]) => void;
  onPickSource?: (cls: string, models: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const v = raw.trim();
    if (!v) return;
    if (!value.some((x) => x.toLowerCase() === v.toLowerCase()))
      onChange([...value, v]);
  };
  const remove = (c: string) => onChange(value.filter((x) => x !== c));
  const fresh = suggestions.filter(
    (s) => !value.some((v) => v.toLowerCase() === s.cls.toLowerCase()),
  );
  const commitDraft = () => {
    draft
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
      .forEach(add);
    setDraft("");
  };
  return (
    <div className="tag-input">
      {value.length ? (
        <div className="tag-rows">
          {value.map((v) => (
            <span key={v} className="chip blue tag-x">
              {v}
              <button type="button" title="移除" onClick={() => remove(v)}>
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      {fresh.length ? (
        <div className="tag-sugs">
          {fresh.slice(0, 10).map((s) => (
            <button
              key={s.cls}
              type="button"
              className="mini ghost"
              title={
                s.models.length
                  ? `来自模型：${s.models.join("、")}，点击自动绑定`
                  : undefined
              }
              onClick={() => {
                add(s.cls);
                onPickSource?.(s.cls, s.models);
              }}
            >
              + {s.cls}
            </button>
          ))}
          {fresh.length > 10 ? (
            <span className="muted" style={{ fontSize: 11 }}>
              还有 {fresh.length - 10} 个…
            </span>
          ) : null}
        </div>
      ) : null}
      <input
        style={{ width: "100%" }}
        value={draft}
        placeholder="输入类别后回车添加（可逗号分隔多个）"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commitDraft();
          }
        }}
        onBlur={() => setDraft("")}
      />
    </div>
  );
}

/* 参数区：按判定逻辑的参数定义渲染（classes=类别点选，float/int=数字，zones=区域画框） */

function ParamFields({
  spec,
  values,
  classSources,
  cameras,
  onPickSource,
  onChange,
}: {
  spec: TemplateSpec | undefined;
  values: ParamValues;
  classSources: { cls: string; models: string[] }[];
  cameras: Array<{ id: string; name: string; connected: boolean }>;
  onPickSource: (cls: string, models: string[]) => void;
  onChange: (v: ParamValues) => void;
}) {
  if (!spec) return null;
  return (
    <>
      {spec.params.map((p: ParamSpec) => {
        const val = values[p.name] !== undefined ? values[p.name] : p.default;
        if (p.type === "classes") {
          const arr: string[] = Array.isArray(val) ? val : [];
          return (
            <div key={p.name}>
              <label>
                {p.desc || p.name}
                {!p.from_model ? (
                  <span className="muted" style={{ fontSize: 10.5 }}>
                    {" "}
                    · 类别可来自其他模型
                  </span>
                ) : null}
              </label>
              <ClassTagsInput
                value={arr}
                suggestions={classSources}
                onPickSource={onPickSource}
                onChange={(v) => onChange({ ...values, [p.name]: v })}
              />
            </div>
          );
        }
        if (p.type === "zones") {
          const rects = Array.isArray(val)
            ? (val as Array<Record<string, number>>)
            : [];
          return (
            <div key={p.name}>
              <label>{p.desc || p.name}</label>
              <ZoneRectEditor
                value={rects}
                cameras={cameras}
                onChange={(v) => onChange({ ...values, [p.name]: v })}
              />
            </div>
          );
        }
        return (
          <div key={p.name}>
            <label>{p.desc || p.name}</label>
            <input
              className="w240"
              data-param={p.name}
              type="number"
              step="0.05"
              value={String(val ?? "")}
              onChange={(e) => onChange({ ...values, [p.name]: +e.target.value })}
            />
          </div>
        );
      })}
    </>
  );
}

export default function RulesPage() {
  const [rules, setRules] = useState<RuleEntry[] | null>(null);
  // 内部模板注册表：用于把规则的 template 字段解析成判定逻辑 + 参数定义
  const [templates, setTemplates] = useState<Record<string, TemplateSpec>>({});
  const [logics, setLogics] = useState<
    Record<string, { label: string; desc: string }>
  >({});
  const [models, setModels] = useState<string[]>([]);
  const [modelClasses, setModelClasses] = useState<Record<string, string[]>>({});
  const [modelEnabled, setModelEnabled] = useState<Record<string, boolean>>({});
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsLoadError, setModelsLoadError] = useState(false);
  // 区域画框编辑器的参考画面来源（启用中的监控）
  const [cameras, setCameras] = useState<
    Array<{ id: string; name: string; connected: boolean }>
  >([]);
  const classSources = (() => {
    const m = new Map<string, string[]>();
    for (const [name, classes] of Object.entries(modelClasses))
      for (const c of classes) m.set(c, [...(m.get(c) || []), name]);
    return [...m.entries()].map(([cls, ms]) => ({ cls, models: ms }));
  })();
  const [summary, setSummary] = useState<
    Record<string, { false_positive_rate: number | null }> | null
  >(null);
  // 画布节点注册表（GET /api/rules/node-types）：失败时画布编辑器报错并禁用保存
  const [nodeTypes, setNodeTypes] = useState<Record<string, GraphNodeTypeSpec>>({});
  const [nodeTypesFailed, setNodeTypesFailed] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [editingRevision, setEditingRevision] = useState<number | null>(null);
  const [ruleOpen, setRuleOpen] = useState(false);
  const [form, setForm] = useState<RuleForm | null>(null);
  const hasCanvas = Boolean(form && (form.template === "graph" || CONVERTIBLE_TEMPLATES[form.template]));
  const toast = useToast();
  const confirm = useConfirm();
  const { busy, wrap } = useBusy();

  const logicOf = useCallback(
    (template: string) => templates[template]?.logic || template,
    [templates],
  );
  const logicLabel = useCallback(
    (template: string) => {
      const logic = logicOf(template);
      return logics[logic]?.label || logic;
    },
    [templates, logics, logicOf],
  );

  const refresh = useCallback(async () => {
    const [r, s, t] = await Promise.all([
      api<{ rules: RuleEntry[] }>("/api/rules"),
      api<{ by_rule?: Record<string, { false_positive_rate: number | null }> }>(
        "/api/alerts/summary",
      ).catch(() => null),
      api<{ templates: Record<string, TemplateSpec> }>("/api/rules/templates"),
    ]);
    setRules(r.rules);
    setSummary(s?.by_rule || null);
    setTemplates(t.templates);
  }, []);

  useEffect(() => {
    api<{ logics: Record<string, { label: string; desc: string }> }>(
      "/api/rules/template-logics",
    ).then((r) => setLogics(r.logics));
    setModelsLoading(true);
    setModelsLoadError(false);
    api<ModelsResponse>("/api/models").then((r) => {
      const entries = r.models || [];
      setModels(entries.map((m) => m.name));
      setModelEnabled(
        Object.fromEntries(entries.map((m) => [m.name, m.config_enabled !== false])),
      );
      setModelClasses(
        Object.fromEntries(
          entries.map((m) => [m.name, Object.values(m.classes || {})]),
        ),
      );
    }).catch(() => {
      setModelsLoadError(true);
    }).finally(() => {
      setModelsLoading(false);
    });
    api<{
      cameras: Array<{ id: string; name: string; enabled: boolean; connected: boolean }>;
    }>("/api/cameras").then((r) => setCameras(r.cameras.filter((c) => c.enabled)));
    api<NodeTypesResponse>("/api/rules/node-types")
      .then((r) => setNodeTypes(r.node_types || {}))
      .catch(() => setNodeTypesFailed(true));
  }, []);
  usePolling(refresh, 60000);

  /* ---------------- 规则弹窗 ---------------- */

  const cloneGraph = (g: RuleGraph): RuleGraph =>
    JSON.parse(JSON.stringify(g)) as RuleGraph;

  const baseForm = (r?: RuleEntry) => ({
    id: r ? String(r.id) : "",
    name: r?.name || "",
    description: r?.description || "",
    models: r?.models || [],
    params: r?.params || {},
    severity: String(Math.min(4, Math.max(1, r?.severity ?? 3))),
    enabled: r ? r.enabled : true,
  });

  /* 编辑存量规则：template=graph 直接进画布，老模板保持"何时告警"表单 */
  const openRuleEdit = async (id: number | null) => {
    setEditing(id);
    setEditingRevision(rules?.find((x) => x.id === id)?.revision ?? null);
    if (!rules?.length || !Object.keys(templates).length) await refresh();
    const r = rules?.find((x) => x.id === id);
    const template = r?.template || "graph";
    // 存量可转换模板 → 等价画布（保存时无损回写原模板参数）
    const converted = r && CONVERTIBLE_TEMPLATES[template] && !r.graph
      ? ruleToGraph(template, r.params || {})
      : null;
    setForm({
      ...baseForm(r),
      logic: r ? logicOf(template) || "presence" : "presence",
      template,
      graph: r?.graph
        ? attachGraphModels(cloneGraph(r.graph), r.models || [])
        : converted
          ? attachGraphModels(converted, r?.models || [])
          : { nodes: [], edges: [] },
    });
    setRuleOpen(true);
  };

  /* 从空白画布新建：template 固定 graph，画布取空白图（深拷贝） */
  const openRuleFromPreset = () => {
    setEditing(null);
    setEditingRevision(null);
    setForm({
      ...baseForm(),
      logic: "graph",
      template: "graph",
      graph: cloneGraph(BLANK_PRESET.graph),
    });
    setRuleOpen(true);
  };

  const setFormPatch = (patch: Partial<RuleForm>) =>
    setForm((f) => (f ? { ...f, ...patch } : f));

  const switchLogic = (logic: string) =>
    setForm((f) => (f ? { ...f, logic, params: {} } : f)); // 换逻辑则参数回默认

  const saveRule = wrap("rule", async () => {
    if (!form) return;
    const useCanvas = form.template === "graph" ||
      !!CONVERTIBLE_TEMPLATES[form.template];
    if (useCanvas) {
      const errs = validateGraph(form.graph, nodeTypes, models, form.template === "graph");
      if (errs.length) {
        toast(errs[0], false);
        return;
      }
      // 结构仍是原模板的规范链路 → 参数无损回写，检测行为零变化；
      // 结构被改动 → 转存为独立图规则
      const legacyParams = CONVERTIBLE_TEMPLATES[form.template]
        ? graphToParams(form.template, form.graph)
        : null;
      const graphForSave = legacyParams
        ? form.graph
        : attachGraphModels(form.graph, form.models);
      const body = {
        id: editing ? +form.id : null,
        name: form.name.trim() || "未命名规则",
        description: form.description.trim(),
        template: legacyParams ? (form.template as string) : ("graph" as const),
        models: legacyParams || form.template !== "graph" ? form.models : graphModels(graphForSave),
        params: legacyParams || {},
        severity: +form.severity,
        enabled: form.enabled,
        graph: legacyParams ? undefined : graphForSave,
        ...(editing && editingRevision != null
          ? { expected_revision: editingRevision }
          : {}),
      };
      try {
        if (editing) await api(`/api/rules/${editing}`, { method: "PUT", body });
        else await api("/api/rules", { method: "POST", body });
        toast("已保存，下一帧生效");
        setRuleOpen(false);
        refresh();
      } catch (e) {
        if ((e as { status?: number }).status === 409) {
          toast("配置已被其他请求修改，请刷新后重试", false);
          await refresh();
          setRuleOpen(false);
        } else {
          toast((e as Error).message, false);
        }
      }
      return;
    }
    // 编辑且原模板就是同一判定逻辑 → 保留原模板（尊重自定义参数定义）
    const cur = editing ? rules?.find((x) => x.id === editing) : null;
    const template =
      cur && logicOf(cur.template) === form.logic
        ? cur.template
        : LOGIC_CANONICAL[form.logic] || "generic_presence";
    const body = {
      id: +form.id || null,
      name: form.name.trim() || "未命名规则",
      description: form.description.trim(),
      template,
      models: form.models,
      params: form.params,
      severity: +form.severity,
      enabled: form.enabled,
      ...(editing && editingRevision != null
        ? { expected_revision: editingRevision }
        : {}),
    };
    try {
      if (editing) await api(`/api/rules/${editing}`, { method: "PUT", body });
      else await api("/api/rules", { method: "POST", body });
      toast("已保存，下一帧生效");
      setRuleOpen(false);
      refresh();
    } catch (e) {
      if ((e as { status?: number }).status === 409) {
        toast("配置已被其他请求修改，请刷新后重试", false);
        await refresh();
        setRuleOpen(false);
      } else {
        toast((e as Error).message, false);
      }
    }
  });

  const toggleRule = async (id: number, enable: boolean) => {
    try {
      const revision = rules?.find((r) => r.id === id)?.revision;
      await api(`/api/rules/${id}`, { method: "PUT", body: { enabled: enable, expected_revision: revision } });
      toast("已生效");
      refresh();
    } catch (e) {
      if ((e as { status?: number }).status === 409) {
        toast("配置已被其他请求修改，请刷新后重试", false);
        await refresh();
      } else {
        toast((e as Error).message, false);
      }
    }
  };

  const delRule = async (id: number) => {
    if (!(await confirm(`删除规则 R${id}？历史告警记录会保留。`))) return;
    try {
      const revision = rules?.find((r) => r.id === id)?.revision;
      const suffix = revision == null ? "" : `?expected_revision=${revision}`;
      await api(`/api/rules/${id}${suffix}`, { method: "DELETE" });
      toast("已删除");
      refresh();
    } catch (e) {
      if ((e as { status?: number }).status === 409) {
        toast("配置已被其他请求修改，请刷新后重试", false);
        await refresh();
      } else {
        toast((e as Error).message, false);
      }
    }
  };

  /* ---------------- 渲染 ---------------- */

  const rate = (id: number) => {
    if (!summary) return "—";
    const e = summary[String(id)] || summary[id];
    if (!e || e.false_positive_rate == null) return <span className="muted">未复核</span>;
    const pct = (e.false_positive_rate * 100).toFixed(0) + "%";
    return e.false_positive_rate > 0.5 ? (
      <span style={{ color: "var(--red)", fontWeight: 600 }}>{pct}</span>
    ) : (
      pct
    );
  };

  const specForLogic = (logic: string) => {
    if (!form) return undefined;
    const cur = editing ? rules?.find((x) => x.id === editing) : null;
    if (cur && logicOf(cur.template) === logic)
      return templates[cur.template]; // 保留自定义参数定义
    return templates[LOGIC_CANONICAL[logic]];
  };

  /* 画布规则校验：不满足时禁用保存（节点库失败也禁用） */
  const graphErrs =
    form && form.template === "graph" && !nodeTypesFailed
              ? validateGraph(form.graph, nodeTypes, models, form.template === "graph")
      : [];

  return (
    <Page
      title="规则配置"
      subtitle="从空白画布搭建检测逻辑；模型和类别在检测节点中配置"
      actions={
        <button onClick={openRuleFromPreset} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Icon name="plus" size={13} /> 新建规则
        </button>
      }
    >
      <div className="card">
        {rules === null ? (
          <Empty>加载中…</Empty>
        ) : rules.length ? (
          rules.map((r) => (
            <div className={"rule-item" + (r.enabled ? "" : " off")} key={r.id}>
              <div className="row1">
                <span className="rid">R{String(r.id).padStart(2, "0")}</span>
                <b title={r.name}>{r.name}</b>
                {r.enabled ? <Chip text="启用" color="green" /> : <Chip text="停用" />}
                <span className="ops">
                  <button className="mini ghost" onClick={() => openRuleEdit(r.id)}>
                    编辑
                  </button>
                  <button className="mini ghost" onClick={() => toggleRule(r.id, !r.enabled)}>
                    {r.enabled ? "停用" : "启用"}
                  </button>
                  <button className="mini danger" onClick={() => delRule(r.id)}>
                    删除
                  </button>
                </span>
              </div>
              {r.description ? <div className="desc">{r.description}</div> : null}
              {r.warnings?.length ? (
                <div className="warn"><Icon name="alert-triangle" size={12} /> {r.warnings.join("；")}</div>
              ) : null}
              <div className="meta">
                {r.template === "graph" ? (
                  <>
                    <Chip text="自定义组合" color="blue" />
                    {r.graph ? <span>{r.graph.nodes.length} 个节点</span> : null}
                  </>
                ) : (
                  <span>{logicLabel(r.template)}</span>
                )}
                <span>
                  检测类别{" "}
                  {(() => {
                    const classes = ruleClasses(r);
                    return classes.length
                      ? classes.map((cls) => <Chip key={cls} text={cls} />)
                      : <span className="muted">未配置</span>;
                  })()}
                </span>
                <span>{r.cameras.length ? `${r.cameras.length} 路监控` : "未分配监控"}</span>
                <span>误报率 {rate(r.id)}</span>
              </div>
            </div>
          ))
        ) : (
          <Empty>暂无规则，点右上角「新建规则」</Empty>
        )}
      </div>

      {/* 规则弹窗 */}
      {ruleOpen && form && (
        <Modal
          title={
            editing
              ? `编辑规则 · R${editing}`
              : form.template === "graph"
                ? "新建规则 · 画布"
                : "新建规则"
          }
          width={hasCanvas ? 1140 : 860}
          tall={hasCanvas}
          onClose={() => setRuleOpen(false)}
          footer={
            <>
              <button className="ghost" onClick={() => setRuleOpen(false)}>
                取消
              </button>
              <button
                disabled={
                  busy.rule ||
                  (form.template === "graph" &&
                    (nodeTypesFailed || graphErrs.length > 0))
                }
                onClick={saveRule}
                title={
                  form.template === "graph" && graphErrs.length
                    ? graphErrs[0]
                    : undefined
                }
              >
                保存
              </button>
            </>
          }
        >
          <div className={"rule-form" + (hasCanvas ? " graph-rule-form" : "")}>
            <div className="pane-min">
              {hasCanvas ? (
                <div className="rule-section-heading">
                  <span className="rule-step">1</span>
                  <span>基本信息</span>
                </div>
              ) : null}
              <div className="form-grid">
                <div>
                  <label>规则编号</label>
                  <div className="readonly-value">
                    {editing ? `R${String(form.id).padStart(2, "0")}` : "保存后自动分配"}
                  </div>
                </div>
                <div>
                  <label>严重度</label>
                  <select
                    style={{ width: "100%" }}
                    value={form.severity}
                    onChange={(e) => setFormPatch({ severity: e.target.value })}
                  >
                    <option value="1">1 · 低</option>
                    <option value="2">2 · 中</option>
                    <option value="3">3 · 高</option>
                    <option value="4">4 · 严重</option>
                  </select>
                </div>
              </div>
              <div className="rule-name-desc">
                <div>
                  <label>规则名称</label>
                  <input
                    placeholder="如：门口有人靠近"
                    value={form.name}
                    onChange={(e) => setFormPatch({ name: e.target.value })}
                  />
                </div>
                <div>
                  <label>描述</label>
                  <input
                    value={form.description}
                    onChange={(e) => setFormPatch({ description: e.target.value })}
                  />
                </div>
              </div>
              {form.template !== "graph" &&
              !CONVERTIBLE_TEMPLATES[form.template] ? (
                <>
                  <label>何时告警（判定逻辑）</label>
                  <select
                    style={{ width: "100%" }}
                    value={form.logic}
                    onChange={(e) => switchLogic(e.target.value)}
                  >
                    {Object.entries(logics).map(([k, d]) => (
                      <option key={k} value={k}>
                        {d.label} — {d.desc}
                      </option>
                    ))}
                  </select>
                </>
              ) : null}
              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  color: "var(--text)",
                  marginTop: 14,
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setFormPatch({ enabled: e.target.checked })}
                />{" "}
                启用
              </label>
            </div>
            <div className="pane-min">
              {hasCanvas ? (
                <div className="rule-section-heading">
                  <span className="rule-step">2</span>
                  <span>规则</span>
                  <span className="rule-help">
                    <button type="button" className="rule-help-button" aria-label="规则画布使用说明">
                      <Icon name="help-circle" size={15} />
                    </button>
                    <span className="rule-help-popover" role="tooltip">
                      <b>使用说明</b>
                      <span>点击左侧积木添加节点</span>
                      <span>拖拽节点卡片调整位置</span>
                      <span>点击输出点，再点目标节点连线</span>
                      <span>点击连线可删除</span>
                      <span>选中节点后在右侧编辑参数</span>
                    </span>
                  </span>
                </div>
              ) : null}
              {hasCanvas ? null : (
                <>
                  <label>绑定模型（点选类别时自动勾选来源模型；也可手动调整）</label>
                  <div className="inline-checks">
                    {models.map((m) => (
                      <label key={m}>
                        <input
                          type="checkbox"
                          checked={form.models.includes(m)}
                          onChange={() =>
                            setFormPatch({
                              models: form.models.includes(m)
                                ? form.models.filter((x) => x !== m)
                                : [...form.models, m],
                            })
                          }
                        />{" "}
                        {m}
                      </label>
                    ))}
                  </div>
                </>
              )}
              {form.template !== "graph" &&
              !CONVERTIBLE_TEMPLATES[form.template] ? (
                <ParamFields
                  spec={specForLogic(form.logic)}
                  values={form.params}
                  classSources={classSources}
                  cameras={cameras}
                  onPickSource={(cls, srcModels) => {
                    if (!srcModels.length) return;
                    setFormPatch({
                      models: [...new Set([...form.models, ...srcModels])],
                    });
                  }}
                  onChange={(params) => setFormPatch({ params })}
                />
              ) : null}
            </div>
          </div>
          {hasCanvas ? (
            <GraphEditor
              graph={form.graph}
              onChange={(g) => setFormPatch({ graph: g })}
              nodeTypes={nodeTypes}
              models={models}
              modelEnabled={modelEnabled}
              modelsLoading={modelsLoading}
              modelsLoadError={modelsLoadError}
              modelClasses={modelClasses}
              cameras={cameras}
              loadError={nodeTypesFailed}
              loading={!Object.keys(nodeTypes).length && !nodeTypesFailed}
              requireModels={form.template === "graph"}
            />
          ) : null}
        </Modal>
      )}
    </Page>
  );
}
