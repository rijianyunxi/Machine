import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ParamSpec, RuleEntry, TemplateSpec } from "../api/types";
import { Page } from "../layout/Page";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Chip, Empty, useBusy } from "../ui/badges";

type ParamValues = Record<string, unknown>;

interface ParamRow {
  name: string;
  type: string;
  default: string; // 输入框中的字符串形态
  min: string;
  max: string;
  desc: string;
  from_model: boolean;
}

interface RuleForm {
  id: string;
  name: string;
  description: string;
  template: string;
  models: string[];
  params: ParamValues;
  severity: string;
  enabled: boolean;
}

interface TplForm {
  name: string;
  label: string;
  logic: string;
  params: ParamRow[];
}

function rowToParam(p: ParamRow): Record<string, unknown> {
  const out: Record<string, unknown> = {
    name: p.name.trim(),
    type: p.type,
    desc: p.desc.trim(),
    from_model: p.from_model,
  };
  if (p.type === "classes") {
    out.default = p.default.trim()
      ? p.default.split(",").map((x) => x.trim()).filter(Boolean)
      : [];
  } else {
    out.default = p.default.trim() === "" ? 0 : +p.default;
    if (p.min !== "") out.min = +p.min;
    if (p.max !== "") out.max = +p.max;
  }
  return out;
}

/* 规则弹窗的参数区：按模板 spec 渲染（classes=逗号文本，float/int=数字） */
function ParamFields({
  spec,
  values,
  onChange,
}: {
  spec: TemplateSpec | undefined;
  values: ParamValues;
  onChange: (v: ParamValues) => void;
}) {
  if (!spec) return null;
  return (
    <>
      {spec.params.map((p: ParamSpec) => {
        const val = values[p.name] !== undefined ? values[p.name] : p.default;
        if (p.type === "classes" || p.type === "list") {
          const s = Array.isArray(val) ? val.join(", ") : String(val ?? "");
          return (
            <div key={p.name}>
              <label>{p.desc || p.name}</label>
              <input
                style={{ width: "100%" }}
                data-param={p.name}
                value={s}
                placeholder="类别名用英文逗号分隔"
                onChange={(e) =>
                  onChange({
                    ...values,
                    [p.name]: e.target.value
                      .split(",")
                      .map((x) => x.trim())
                      .filter(Boolean),
                  })
                }
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
  const [templates, setTemplates] = useState<Record<string, TemplateSpec>>({});
  const [logics, setLogics] = useState<Record<string, string>>({});
  const [models, setModels] = useState<string[]>([]);
  const [summary, setSummary] = useState<Record<string, { false_positive_rate: number | null }> | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [ruleOpen, setRuleOpen] = useState(false);
  const [form, setForm] = useState<RuleForm | null>(null);
  const [tplEditing, setTplEditing] = useState<string | null>(null);
  const [tplOpen, setTplOpen] = useState(false);
  const [tpl, setTpl] = useState<TplForm | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const { busy, wrap } = useBusy();

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
    api<{ templates: Record<string, TemplateSpec> }>("/api/rules/templates");
    api<{ logics: Record<string, string> }>("/api/rules/template-logics").then((r) =>
      setLogics(r.logics),
    );
    api<{ models: Array<{ name: string }> }>("/api/models").then((r) =>
      setModels(r.models.map((m) => m.name)),
    );
  }, []);
  usePolling(refresh, 60000);

  /* ---------------- 规则弹窗 ---------------- */

  const openRuleEdit = async (id: number | null) => {
    setEditing(id);
    if (!rules?.length) await refresh();
    const r = rules?.find((x) => x.id === id);
    const tplKeys = Object.keys(templates);
    const tplName = r?.template || tplKeys[0] || "";
    setForm({
      id: r ? String(r.id) : "",
      name: r?.name || "",
      description: r?.description || "",
      template: tplName,
      models: r?.models || [],
      params: r?.params || {},
      severity: String(r?.severity ?? 3),
      enabled: r ? r.enabled : true,
    });
    setRuleOpen(true);
  };

  const setFormPatch = (patch: Partial<RuleForm>) =>
    setForm((f) => (f ? { ...f, ...patch } : f));

  const saveRule = wrap("rule", async () => {
    if (!form) return;
    const body = {
      id: +form.id || null,
      name: form.name.trim(),
      description: form.description.trim(),
      template: form.template,
      models: form.models,
      params: form.params,
      severity: +form.severity,
      enabled: form.enabled,
    };
    try {
      if (editing)
        await api(`/api/rules/${editing}`, { method: "PUT", body });
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

  /* ---------------- 模板弹窗 ---------------- */

  const openTplEdit = (name: string | null) => {
    setTplEditing(name);
    const t = name ? templates[name] : null;
    setTpl({
      name: name || "",
      label: t?.label || "",
      logic: t?.logic || Object.keys(logics)[0] || "",
      params: (t?.params || []).map((p) => ({
        name: p.name,
        type: p.type,
        default: Array.isArray(p.default) ? p.default.join(", ") : String(p.default ?? ""),
        min: p.min !== undefined ? String(p.min) : "",
        max: p.max !== undefined ? String(p.max) : "",
        desc: p.desc || "",
        from_model: !!p.from_model,
      })),
    });
    setTplOpen(true);
  };

  const setTplPatch = (patch: Partial<TplForm>) => setTpl((t) => (t ? { ...t, ...patch } : t));
  const setRow = (i: number, patch: Partial<ParamRow>) =>
    setTpl((t) =>
      t ? { ...t, params: t.params.map((r, ri) => (ri === i ? { ...r, ...patch } : r)) } : t,
    );

  const saveTemplate = wrap("tpl", async () => {
    if (!tpl) return;
    const body = {
      name: tpl.name.trim(),
      label: tpl.label.trim(),
      logic: tpl.logic,
      params: tpl.params.map(rowToParam),
    };
    try {
      if (tplEditing)
        await api(`/api/rules/templates/${encodeURIComponent(tplEditing)}`, { method: "PUT", body });
      else await api("/api/rules/templates", { method: "POST", body });
      toast("模板已保存");
      setTplOpen(false);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const delTpl = async (name: string) => {
    if (!(await confirm(`删除模板 ${name}？`))) return;
    try {
      await api(`/api/rules/templates/${encodeURIComponent(name)}`, { method: "DELETE" });
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

  return (
    <Page
      title="规则配置"
      subtitle="规则与模板都是配置：参数 / 绑定模型 / 分配监控 / 模板类型在线可调，下一帧生效"
      actions={
        <>
          <button className="ghost" onClick={() => openTplEdit(null)}>
            ＋ 新建模板
          </button>
          <button onClick={() => openRuleEdit(null)}>＋ 新建规则</button>
        </>
      }
    >
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>名称</th>
                <th>模板</th>
                <th>绑定模型</th>
                <th>使用监控</th>
                <th>7 天误报率</th>
                <th>状态</th>
                <th style={{ textAlign: "right" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {rules === null ? (
                <tr>
                  <td colSpan={8}>
                    <Empty>加载中…</Empty>
                  </td>
                </tr>
              ) : rules.length ? (
                rules.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">R{String(r.id).padStart(2, "0")}</td>
                    <td>
                      <b>{r.name}</b>
                      {r.warnings?.length ? (
                        <div className="muted" style={{ color: "var(--yellow)", fontSize: 11.5 }}>
                          ⚠ {r.warnings.join("；")}
                        </div>
                      ) : null}
                      <div className="muted" style={{ fontSize: 11.5 }}>
                        {r.description}
                      </div>
                    </td>
                    <td className="muted">{templates[r.template]?.label || r.template}</td>
                    <td>
                      {r.models.length
                        ? r.models.map((m) => <Chip key={m} text={m} color="blue" />)
                        : <Chip text="全部模型" />}
                    </td>
                    <td>
                      {r.cameras.length ? (
                        <span className="muted">{r.cameras.length} 路</span>
                      ) : (
                        <span className="muted">未分配</span>
                      )}
                    </td>
                    <td>{rate(r.id)}</td>
                    <td>{r.enabled ? <Chip text="启用" color="green" /> : <Chip text="停用" />}</td>
                    <td className="actions" style={{ textAlign: "right" }}>
                      <button className="mini ghost" onClick={() => openRuleEdit(r.id)}>
                        编辑
                      </button>
                      <button className="mini ghost" onClick={() => toggleRule(r.id, !r.enabled)}>
                        {r.enabled ? "停用" : "启用"}
                      </button>
                      <button className="mini danger" onClick={() => delRule(r.id)}>
                        删除
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8}>
                    <Empty>暂无规则</Empty>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="tpl-head">
          <h3>模板管理</h3>
          <span className="muted" style={{ fontSize: 12 }}>
            模板 = 检测原语 + 参数定义；新增模板类型无需改代码
          </span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>模板名</th>
                <th>显示名称</th>
                <th>检测原语</th>
                <th>参数</th>
                <th>被规则引用</th>
                <th style={{ textAlign: "right" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(templates).length ? (
                Object.entries(templates).map(([k, v]) => {
                  const usage = rules?.filter((r) => r.template === k).length ?? 0;
                  return (
                    <tr key={k}>
                      <td className="mono">{k}</td>
                      <td>
                        <b>{v.label}</b>
                      </td>
                      <td className="muted" style={{ maxWidth: 360, fontSize: 11.5 }}>
                        <Chip text={v.logic || "?"} color="blue" /> {logics[v.logic] || ""}
                      </td>
                      <td className="muted">{(v.params || []).length} 项</td>
                      <td>
                        {usage ? (
                          <span className="muted">{usage} 条</span>
                        ) : (
                          <span className="muted">未引用</span>
                        )}
                      </td>
                      <td className="actions" style={{ textAlign: "right" }}>
                        <button className="mini ghost" onClick={() => openTplEdit(k)}>
                          编辑
                        </button>
                        <button className="mini danger" onClick={() => delTpl(k)}>
                          删除
                        </button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={6}>
                    <Empty>暂无模板</Empty>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 规则弹窗 */}
      {ruleOpen && form && (
        <Modal
          title={editing ? `编辑规则 · R${editing}` : "新建规则"}
          width={640}
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
          <div className="form-grid">
            <div>
              <label>规则 ID（留空自动分配）</label>
              <input
                className="w240"
                type="number"
                disabled={!!editing}
                value={form.id}
                onChange={(e) => setFormPatch({ id: e.target.value })}
              />
            </div>
            <div>
              <label>严重度（1 低 ~ 4 严重）</label>
              <input
                className="w240"
                type="number"
                min={1}
                max={4}
                value={form.severity}
                onChange={(e) => setFormPatch({ severity: e.target.value })}
              />
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
          <label>模板类型</label>
          <select
            style={{ width: "100%" }}
            value={form.template}
            onChange={(e) => setFormPatch({ template: e.target.value })}
          >
            {Object.entries(templates).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </select>
          <label>绑定模型（该规则只消费所选模型的检测结果）</label>
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
            spec={templates[form.template]}
            values={form.params}
            onChange={(params) => setFormPatch({ params })}
          />
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
        </Modal>
      )}

      {/* 模板弹窗 */}
      {tplOpen && tpl && (
        <Modal
          title={tplEditing ? `编辑模板 · ${tplEditing}` : "新建模板"}
          width={820}
          onClose={() => setTplOpen(false)}
          footer={
            <>
              <button className="ghost" onClick={() => setTplOpen(false)}>
                取消
              </button>
              <button disabled={busy.tpl} onClick={saveTemplate}>
                保存
              </button>
            </>
          }
        >
          <div className="form-grid">
            <div>
              <label>模板名（英文标识，保存后不可改）</label>
              <input
                className="w240"
                placeholder="open_flame"
                disabled={!!tplEditing}
                value={tpl.name}
                onChange={(e) => setTplPatch({ name: e.target.value })}
              />
            </div>
            <div>
              <label>显示名称</label>
              <input
                className="w240"
                placeholder="明火检出"
                value={tpl.label}
                onChange={(e) => setTplPatch({ label: e.target.value })}
              />
            </div>
          </div>
          <label>检测原语（决定判定逻辑，参数只负责配置）</label>
          <select
            style={{ width: "100%" }}
            value={tpl.logic}
            onChange={(e) => setTplPatch({ logic: e.target.value })}
          >
            {Object.entries(logics).map(([k, d]) => (
              <option key={k} value={k}>
                {k} — {d}
              </option>
            ))}
          </select>
          <label style={{ marginTop: 16 }}>
            参数定义（规则实例的编辑表单按此渲染）
          </label>
          <div className="tpl-param-grid header">
            <span>参数名</span>
            <span>类型</span>
            <span>默认值</span>
            <span>min</span>
            <span>max</span>
            <span>说明</span>
            <span>模型校验</span>
            <span />
          </div>
          {tpl.params.map((row, i) => (
            <div className="tpl-param-grid" key={i}>
              <input
                placeholder="参数名"
                value={row.name}
                onChange={(e) => setRow(i, { name: e.target.value })}
              />
              <select
                value={row.type}
                onChange={(e) => setRow(i, { type: e.target.value })}
              >
                <option value="classes">classes</option>
                <option value="float">float</option>
                <option value="int">int</option>
              </select>
              <input
                type={row.type === "classes" ? "text" : "number"}
                step={row.type === "classes" ? undefined : "any"}
                placeholder={row.type === "classes" ? "类别名用英文逗号分隔" : "默认数值"}
                value={row.default}
                onChange={(e) => setRow(i, { default: e.target.value })}
              />
              <input
                type="number"
                placeholder="min"
                value={row.min}
                onChange={(e) => setRow(i, { min: e.target.value })}
              />
              <input
                type="number"
                placeholder="max"
                value={row.max}
                onChange={(e) => setRow(i, { max: e.target.value })}
              />
              <input
                placeholder="表单里显示的说明"
                value={row.desc}
                onChange={(e) => setRow(i, { desc: e.target.value })}
              />
              <label className="fm">
                <input
                  type="checkbox"
                  checked={row.from_model}
                  onChange={(e) => setRow(i, { from_model: e.target.checked })}
                />{" "}
                校验
              </label>
              <button
                className="mini danger"
                title="移除参数"
                onClick={() =>
                  setTpl((t) =>
                    t ? { ...t, params: t.params.filter((_, ri) => ri !== i) } : t,
                  )
                }
              >
                ✕
              </button>
            </div>
          ))}
          <button
            className="mini ghost"
            style={{ marginTop: 6 }}
            onClick={() =>
              setTpl((t) =>
                t
                  ? {
                      ...t,
                      params: [
                        ...t.params,
                        {
                          name: "",
                          type: "classes",
                          default: "",
                          min: "",
                          max: "",
                          desc: "",
                          from_model: false,
                        },
                      ],
                    }
                  : t,
              )
            }
          >
            ＋ 添加参数
          </button>
          <p className="muted" style={{ fontSize: 11.5, marginTop: 12 }}>
            「模型校验」勾选后，规则绑定模型的类别列表会校验该参数（如 hardhat
            必须在绑定模型里）。被规则引用的模板不能删除；改参数定义不影响已保存规则的参数值。
          </p>
        </Modal>
      )}
    </Page>
  );
}
