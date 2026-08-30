import { useCallback, useEffect, useRef, useState } from "react";
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
/* 区域画框编辑器：取监控当前帧（无帧则 16:9 灰底），拖拽框选告警区域，
 * 归一化 x/y/w/h 存储（左上角原点）。 */
function ZoneRectEditor({
  value,
  cameras,
  onChange,
}: {
  value: Array<Record<string, number>>;
  cameras: Array<{ id: string; name: string; connected: boolean }>;
  onChange: (v: Array<Record<string, number>>) => void;
}) {
  const [camId, setCamId] = useState(cameras[0]?.id || "");
  const [ghost, setGhost] = useState<null | {
    x: number;
    y: number;
    w: number;
    h: number;
  }>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<null | { x0: number; y0: number }>(null);

  const norm = (e: MouseEvent | React.MouseEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    return {
      x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
      y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1),
    };
  };

  /* 拖拽全程挂 document：鼠标移出画布也能继续框选 */
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d || !stageRef.current) return;
      const { x, y } = norm(e);
      setGhost({
        x: Math.min(d.x0, x),
        y: Math.min(d.y0, y),
        w: Math.abs(x - d.x0),
        h: Math.abs(y - d.y0),
      });
    };
    const onUp = (e: MouseEvent) => {
      const d = dragRef.current;
      dragRef.current = null;
      if (!d || !stageRef.current) return;
      const { x, y } = norm(e);
      const w = Math.abs(x - d.x0);
      const h = Math.abs(y - d.y0);
      if (w > 0.02 && h > 0.02)
        onChange([
          ...value,
          { x: Math.min(d.x0, x), y: Math.min(d.y0, y), w, h },
        ]);
      setGhost(null);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  });

  const frameUrl = camId
    ? `/api/cameras/${encodeURIComponent(camId)}/frame.jpg?w=960`
    : "";

  return (
    <div className="zone-editor">
      <div style={{ padding: "8px 10px", display: "flex", gap: 10, alignItems: "center" }}>
        <span className="muted" style={{ fontSize: 11.5 }}>
          参考画面
        </span>
        <select
          style={{ minWidth: 140 }}
          value={camId}
          onChange={(e) => setCamId(e.target.value)}
        >
          {cameras.length ? (
            cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name || c.id}
              </option>
            ))
          ) : (
            <option value="">无可用监控</option>
          )}
        </select>
        <span className="muted" style={{ fontSize: 11.5, marginLeft: "auto" }}>
          在画面上拖拽框选区域，共 {value.length} 个
        </span>
      </div>
      <div
        ref={stageRef}
        className="zone-stage"
        onMouseDown={(e) => {
          if (e.target !== stageRef.current && !(e.target as HTMLElement).classList.contains("zone-hint"))
            return;
          const { x, y } = norm(e);
          dragRef.current = { x0: x, y0: y };
          e.preventDefault();
        }}
      >
        {camId ? (
          <img
            src={frameUrl}
            alt=""
            draggable={false}
            onError={(e) => {
              (e.target as HTMLImageElement).style.visibility = "hidden";
            }}
          />
        ) : null}
        <div className="zone-hint">
          {camId ? "在画面上按住拖拽，框出告警区域" : "选择监控后取画面框选；或直接按画面比例框选"}
        </div>
        {value.map((z, i) => (
          <div
            key={i}
            className="zone-rect"
            style={{
              left: `${z.x * 100}%`,
              top: `${z.y * 100}%`,
              width: `${z.w * 100}%`,
              height: `${z.h * 100}%`,
            }}
          >
            <button
              type="button"
              className="zx"
              title="删除此区域"
              onClick={() => onChange(value.filter((_, zi) => zi !== i))}
            >
              ×
            </button>
          </div>
        ))}
        {ghost ? (
          <div
            className="zone-ghost"
            style={{
              left: `${ghost.x * 100}%`,
              top: `${ghost.y * 100}%`,
              width: `${ghost.w * 100}%`,
              height: `${ghost.h * 100}%`,
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

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
    api<{
      cameras: Array<{ id: string; name: string; enabled: boolean; connected: boolean }>;
    }>("/api/cameras").then((r) => setCameras(r.cameras.filter((c) => c.enabled)));
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
      name: form.name.trim() || "未命名规则",
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
                    // 只聚合字符串数组（类别参数）；zones 等对象数组跳过
                    const clsParams = Object.entries(r.params).filter(
                      ([, v]) =>
                        Array.isArray(v) && v.every((x) => typeof x === "string"),
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
              <label>规则名称</label>
              <input
                className="w320"
                placeholder="如：门口有人靠近"
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
                cameras={cameras}
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
