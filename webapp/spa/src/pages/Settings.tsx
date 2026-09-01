import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { SettingsResponse } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { useToast } from "../ui/Toast";
import { Chip, Empty, useBusy } from "../ui/badges";

type Values = Record<string, Record<string, unknown>>;

type SettingsTab = {
  key: string;
  label: string;
  sections: string[];
};

const SETTING_TABS: SettingsTab[] = [
  { key: "runtime", label: "运行与告警", sections: ["capture", "snapshot", "alert"] },
  { key: "system", label: "系统与面板", sections: ["logging", "database", "panel"] },
  { key: "llm", label: "大模型 (LLM)", sections: ["llm"] },
];

export default function SettingsPage() {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [values, setValues] = useState<Values>({});
  const [llmModels, setLlmModels] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState("runtime");
  const toast = useToast();
  const { busy, wrap } = useBusy();

  const refresh = useCallback(async () => {
    const d = await api<SettingsResponse>("/api/settings");
    setData(d);
    setActiveTab((current) =>
      SETTING_TABS.some((tab) => tab.key === current) ? current : SETTING_TABS[0].key,
    );
    // Keep unsaved drafts in the form, while filling newly loaded/missing keys
    // from the server. A save below explicitly replaces the saved section.
    setValues((prev) => {
      const next: Values = {};
      for (const [section, s] of Object.entries(d.sections)) {
        next[section] = { ...(prev[section] || {}) };
        for (const k of s.keys) {
          if (next[section][k.key] === undefined) next[section][k.key] = k.value;
        }
      }
      return next;
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const setVal = (section: string, key: string, v: unknown) =>
    setValues((vals) => ({ ...vals, [section]: { ...vals[section], [key]: v } }));

  const [savingSec, setSavingSec] = useState<string | null>(null);

  const doSave = async (section: string): Promise<boolean> => {
    if (savingSec) return false;
    const vals = values[section] || {};
    const body: Record<string, unknown> = {};
    for (const k of data?.sections[section]?.keys || []) {
      const v = vals[k.key];
      if (k.type === "int" || k.type === "float") {
        const raw = String(v ?? "").trim();
        const n = k.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
        if (raw === "" || !Number.isFinite(n)) {
          toast(`「${k.desc || k.key}」需为数字${raw ? `（当前 "${raw}"）` : ""}`, false);
          return false;
        }
        body[k.key] = n;
      } else {
        body[k.key] = v;
      }
    }
    setSavingSec(section);
    try {
      const r = await api<{ restart_required: boolean; values: Record<string, unknown> }>(
        `/api/settings/${section}`,
        { method: "PUT", body },
      );
      // Keep the form aligned with the values accepted by the server (for
      // example int/float coercion) before refreshing the restart banner.
      setValues((prev) => ({
        ...prev,
        [section]: { ...prev[section], ...r.values },
      }));
      toast(r.restart_required ? "已保存（部分项需重启完全生效）" : "已保存并热生效");
      await refresh();
      return true;
    } catch (e) {
      toast((e as Error).message, false);
      return false;
    } finally {
      setSavingSec(null);
    }
  };

  const testLlm = wrap("llmtest", async () => {
    if (!(await doSave("llm"))) return; // 测试前先保存，保证用屏显配置
    try {
      const r = await api<{ reply: string }>("/api/llm/test", { method: "POST", body: {} });
      toast(`LLM 连接正常：${r.reply}`);
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const fetchLlmModels = wrap("llmmodels", async () => {
    if (!(await doSave("llm"))) return; // 先保存，endpoint/key 以屏显为准
    try {
      const r = await api<{ models: string[] }>("/api/llm/models", { method: "POST", body: {} });
      setLlmModels(r.models);
      toast(`获取到 ${r.models.length} 个模型`);
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  if (!data) {
    return (
      <Page title="系统设置" subtitle="所有配置落盘 settings.yaml 并尽可能热生效；改动前自动备份">
        <div className="card">
          <Empty>加载中…</Empty>
        </div>
      </Page>
    );
  }

  const pend = Object.keys(data.pending_restart || {});
  const currentTab = SETTING_TABS.find((tab) => tab.key === activeTab) || SETTING_TABS[0];
  const visibleSections = Object.entries(data.sections).filter(([section]) =>
    currentTab.sections.includes(section),
  );

  return (
    <Page
      title="系统设置"
      subtitle="所有配置落盘 settings.yaml 并尽可能热生效；改动前自动备份"
    >
      {pend.length ? (
        <div className="banner">
          <Icon name="alert-triangle" size={15} />
          <span>以下配置段需重启主程序才能完全生效：{pend.join("、")}</span>
        </div>
      ) : null}
      <div className="settings-tabs" role="tablist" aria-label="设置分类">
        {SETTING_TABS.map((tab) => {
          const tabSections = tab.sections.filter((section) => data.sections[section]);
          const needsRestart = tabSections.some((section) => data.sections[section].restart_required);
          return (
            <button
              key={tab.key}
              className={`settings-tab ${tab.key === currentTab.key ? "on" : ""}`}
              role="tab"
              aria-selected={tab.key === currentTab.key}
              onClick={() => setActiveTab(tab.key)}
            >
              <span>{tab.label}</span>
              <Chip
                text={needsRestart ? "含需重启" : "即时生效"}
                color={needsRestart ? "yellow" : "green"}
              />
            </button>
          );
        })}
      </div>
      <div
        className="grid settings-grid"
        style={{ gridTemplateColumns: "repeat(auto-fit,minmax(360px,1fr))" }}
        role="tabpanel"
      >
        {visibleSections.map(([section, s]) => (
          <div className="card" key={section}>
            <div className="card-title" style={{ marginBottom: 4 }}>
              <span>{s.label}</span>
              {s.restart_required ? (
                <Chip text="部分需重启" color="yellow" />
              ) : (
                <Chip text="即时生效" color="green" />
              )}
            </div>
            {s.keys.map((k) => (
              <div key={k.key}>
                <label>
                  {k.desc}{" "}
                  <span className="mono muted" style={{ fontSize: 10.5 }}>
                    {section}.{k.key}
                  </span>
                </label>
                {k.type === "bool" ? (
                  <input
                    type="checkbox"
                    checked={!!values[section]?.[k.key]}
                    onChange={(e) => setVal(section, k.key, e.target.checked)}
                  />
                ) : (
                  <input
                    type={section === "llm" && k.key === "api_key" ? "password" : "text"}
                    style={{ width: "100%" }}
                    value={String(values[section]?.[k.key] ?? "")}
                    onChange={(e) => setVal(section, k.key, e.target.value)}
                  />
                )}
              </div>
            ))}
            {section === "llm" ? (
              <div
                style={{
                  marginTop: 14,
                  borderTop: "1px solid var(--border)",
                  paddingTop: 12,
                }}
              >
                <label>从服务获取模型列表（先保存配置再获取）</label>
                <div className="toolbar">
                  <select
                    style={{ flex: 1, minWidth: 160 }}
                    value={llmModels.includes(String(values.llm?.model ?? ""))
                      ? String(values.llm?.model ?? "")
                      : ""}
                    onChange={(e) => {
                      if (!e.target.value) return;
                      setVal("llm", "model", e.target.value);
                      toast(`已填入模型：${e.target.value}，请点保存`);
                    }}
                  >
                    <option value="">— {llmModels.length ? "选择模型" : "点「获取模型」拉取"} —</option>
                    {llmModels.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <button
                    className="mini ghost"
                    disabled={busy.llmmodels}
                    onClick={fetchLlmModels}
                  >
                    保存并获取模型
                  </button>
                </div>
                <p className="muted" style={{ marginTop: 6 }}>
                  选中模型后记得点上方「保存」。
                </p>
              </div>
            ) : null}
            <div
              style={{
                marginTop: 16,
                display: "flex",
                gap: 10,
                justifyContent: "flex-end",
              }}
            >
              {section === "llm" ? (
                <button className="ghost" disabled={busy.llmtest} onClick={testLlm}>
                  保存并测试连接
                </button>
              ) : null}
              <button
                disabled={savingSec === section}
                onClick={() => doSave(section)}
              >
                保存
              </button>
            </div>
          </div>
        ))}
      </div>
    </Page>
  );
}
