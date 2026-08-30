import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  ModelsResponse,
  ParamSpec,
  RuleEntry,
  TemplateSpec,
} from "../api/types";
import { Page } from "../layout/Page";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Chip, Empty, useBusy } from "../ui/badges";

type ParamValues = Record<string, unknown>;

/* 判定逻辑 -> 规则落盘时使用的内部模板名（模板已从界面隐藏，仅作存储机制） */
const LOGIC_CANONICAL: Record<string, string> = {
  presence: "generic_presence",
  presence_near: "presence_near_person",
  absence_required: "ppe_absence",
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

/* 参数区：按判定逻辑的参数定义渲染（classes=类别点选，float/int=数字） */
function ParamFields({
  spec,
  values,
  classSources,
  onPickSource,
  onChange,
}: {
  spec: TemplateSpec | undefined;
  values: ParamValues;
  classSources: { cls: string; models: string[] }[];
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
  const classSources = (() => {
    const m = new Map<string, string[]>();
    for (const [name, classes] of Object.entries(modelClasses))
      for (const c of classes) m.set(c, [...(m.get(c) || []), name]);
    return [...m.entries()].map(([cls, ms]) => ({ cls, models: ms }));
  })();
  const [summary, setSummary] = useState<
    Record<string, { false_positive_rate: number | null }> | null
  >(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [ruleOpen, setRuleOpen] = useState(false);
  const [form, setForm] = useState<RuleForm | null>(null);
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
    api<ModelsResponse>("/api/models").then((r) => {
      setModels(r.models.map((m) => m.name));
      setModelClasses(
        Object.fromEntries(
          r.models.map((m) => [m.name, Object.values(m.classes || {})]),
        ),
      );
    });
  }, []);
  usePolling(refresh, 60000);

  /* ---------------- 规则弹窗 ---------------- */

  const openRuleEdit = async (id: number | null) => {
    setEditing(id);
    if (!rules?.length || !Object.keys(templates).length) await refresh();
    const r = rules?.find((x) => x.id === id);
    setForm({
      id: r ? String(r.id) : "",
      name: r?.name || "",
      description: r?.description || "",
      logic: r ? logicOf(r.template) || "presence" : "presence",
      models: r?.models || [],
      params: r?.params || {},
      severity: String(Math.min(4, Math.max(1, r?.severity ?? 3))),
      enabled: r ? r.enabled : true,
    });
    setRuleOpen(true);
  };

  const setFormPatch = (patch: Partial<RuleForm>) =>
    setForm((f) => (f ? { ...f, ...patch } : f));

  const switchLogic = (logic: string) =>
    setForm((f) => (f ? { ...f, logic, params: {} } : f)); // 换逻辑则参数回默认

  const saveRule = wrap("rule", async () => {
    if (!form) return;
    // 编辑且原模板就是同一判定逻辑 → 保留原模板（尊重自定义参数定义）
    const cur = editing ? rules?.find((x) => x.id === editing) : null;
    const template =
      cur && logicOf(cur.template) === form.logic
        ? cur.template
        : LOGIC_CANONICAL[form.logic] || "generic_presence";
    const body = {
      id: +form.id || null,
      name: form.name.trim(),
      description: form.description.trim(),
      template,
      models: form.models,
      params: form.params,
      severity: +form.severity,
      enabled: form.enabled,
    };
    try {
      if (editing) await api(`/api/rules/${editing}`, { method: "PUT", body });
      else await api("/api/rules", { method: "POST", body });
      toast("已保存，下一帧生效");
      setRuleOpen(false);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const toggleRule = async (id: number, enable: boolean) => {
    try {
      await api(`/api/rules/${id}`, { method: "PUT", body: { enabled: enable } });
      toast("已生效");
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  };

  const delRule = async (id: number) => {
    if (!(await confirm(`删除规则 R${id}？历史告警记录会保留。`))) return;
    try {
      await api(`/api/rules/${id}`, { method: "DELETE" });
      toast("已删除");
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
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

  return (
    <Page
      title="规则配置"
      subtitle="三步完成一个检测：模型里来类别，规则定何时告警，监控里选哪路画面"
      actions={<button onClick={() => openRuleEdit(null)}>＋ 新建规则</button>}
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
                <div className="warn">⚠ {r.warnings.join("；")}</div>
              ) : null}
              <div className="meta">
                <span>{logicLabel(r.template)}</span>
                <span>
                  类别{" "}
                  {(() => {
                    const clsParams = Object.entries(r.params).filter(
                      ([, v]) => Array.isArray(v),
                    );
                    const all = [
                      ...new Set(
                        clsParams.flatMap(([, v]) => v as string[]),
                      ),
                    ];
                    return all.length
                      ? all.map((c) => <Chip key={c} text={c} />)
                      : "—";
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
          title={editing ? `编辑规则 · R${editing}` : "新建规则"}
          width={860}
          onClose={() => setRuleOpen(false)}
          footer={
            <>
              <button className="ghost" onClick={() => setRuleOpen(false)}>
                取消
              </button>
              <button disabled={busy.rule} onClick={saveRule}>
                保存
              </button>
            </>
          }
        >
          <div className="rule-form">
            <div className="pane-min">
              <div className="form-grid">
                <div>
                  <label>规则 ID（留空自动分配）</label>
                  <input
                    style={{ width: "100%" }}
                    type="number"
                    disabled={!!editing}
                    value={form.id}
                    onChange={(e) => setFormPatch({ id: e.target.value })}
                  />
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
              <label>规则名称（英文标识）</label>
              <input
                className="w320"
                placeholder="no_safety_vest"
                value={form.name}
                onChange={(e) => setFormPatch({ name: e.target.value })}
              />
              <label>描述</label>
              <input
                style={{ width: "100%" }}
                value={form.description}
                onChange={(e) => setFormPatch({ description: e.target.value })}
              />
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
              <ParamFields
                spec={specForLogic(form.logic)}
                values={form.params}
                classSources={classSources}
                onPickSource={(cls, srcModels) => {
                  if (!srcModels.length) return;
                  setFormPatch({
                    models: [...new Set([...form.models, ...srcModels])],
                  });
                }}
                onChange={(params) => setFormPatch({ params })}
              />
            </div>
          </div>
        </Modal>
      )}
    </Page>
  );
}
