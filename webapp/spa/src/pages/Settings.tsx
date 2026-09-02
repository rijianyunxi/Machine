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
  const [llmModelsOpen, setLlmModelsOpen] = useState(false);
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
    const sectionRevision = data?.sections[section]?.revision;
    if (sectionRevision != null) body.expected_revision = sectionRevision;
    setSavingSec(section);
    try {
      const r = await api<{
        restart_required: boolean;
        values: Record<string, unknown>;
        revision: number;
      }>(
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
      if ((e as { status?: number }).status === 409) {
        toast("配置已被其他请求修改，请刷新后重试", false);
        setValues({});
        await refresh();
      } else {
        toast((e as Error).message, false);
      }
      return false;
    } finally {
      setSavingSec(null);
    }
  };

  const testLlm = wrap("llmtest", async () => {
    try {
      const r = await api<{ reply: string }>("/api/llm/test", { method: "POST", body: {} });
      toast(`LLM 连接正常：${r.reply}`);
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const fetchLlmModels = wrap("llmmodels", async () => {
    try {
      const r = await api<{ models: string[] }>("/api/llm/models", { method: "POST", body: {} });
      setLlmModels(r.models);
      setLlmModelsOpen(r.models.length > 0);
      toast(`获取到 ${r.models.length} 个模型`);
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const restartService = wrap("restart", async () => {
    if (!window.confirm("确定要重启服务吗？当前正在进行的任务和连接会短暂中断。")) return;
    try {
      await api<{ ok: boolean; message?: string }>("/api/system/restart", {
        method: "POST",
        body: {},
      });
      toast("服务即将重启，页面会短暂断开");
      window.setTimeout(() => window.location.reload(), 1800);
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  if (!data) {
    return (
      <Page title="系统设置" subtitle="配置统一保存到 machine.db 并尽可能热生效；改动前请先完成数据库备份">
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
      subtitle="所有配置保存到 machine.db 并尽可能热生效"
      actions={
        <button
          className="danger"
          disabled={busy.restart}
          onClick={restartService}
          title="重启检测服务，配置中标记为需重启的项目将在重启后生效"
        >
          <Icon name="refresh" size={14} />
          {busy.restart ? "重启中…" : "重启服务"}
        </button>
      }
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
            {s.keys.map((k) => {
              if (section === "llm" && k.key === "model") return null;
              return (
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
                      type={(section === "llm" && k.key === "api_key") ||
                            (section === "panel" && k.key === "password")
                            ? "password"
                            : "text"}
                      style={{ width: "100%" }}
                      value={String(values[section]?.[k.key] ?? "")}
                      onChange={(e) => setVal(section, k.key, e.target.value)}
                    />
                  )}
                </div>
              );
            })}
            {section === "llm" ? (
              <div
                style={{
                  marginTop: 14,
                  borderTop: "1px solid var(--border)",
                  paddingTop: 12,
                }}
              >
                <label>模型名（需支持图片输入）</label>
                <div className="toolbar llm-model-row">
                  <div className="llm-model-picker">
                    <input
                      style={{ width: "100%" }}
                      value={String(values.llm?.model ?? "")}
                      placeholder="输入模型名或点击“获取模型”"
                      aria-haspopup="listbox"
                      aria-expanded={llmModelsOpen}
                      onFocus={() => setLlmModelsOpen(llmModels.length > 0)}
                      onBlur={() => window.setTimeout(() => setLlmModelsOpen(false), 120)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setLlmModelsOpen(false);
                      }}
                      onChange={(e) => {
                        setVal("llm", "model", e.target.value);
                        if (llmModels.length > 0) setLlmModelsOpen(true);
                      }}
                    />
                    {llmModelsOpen && llmModels.length ? (
                      <div className="llm-model-options" role="listbox">
                        {llmModels.map((m) => (
                          <button
                            key={m}
                            type="button"
                            className={`llm-model-option ${String(values.llm?.model ?? "") === m ? "selected" : ""}`}
                            role="option"
                            aria-selected={String(values.llm?.model ?? "") === m}
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => {
                              setVal("llm", "model", m);
                              setLlmModelsOpen(false);
                            }}
                          >
                            {m}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <button
                    className="mini ghost"
                    disabled={busy.llmmodels}
                    onClick={fetchLlmModels}
                  >
                    {busy.llmmodels ? "获取中…" : "获取模型"}
                  </button>
                </div>
                <p className="muted" style={{ marginTop: 6 }}>
                  可直接输入模型名，或点击“获取模型”后从候选列表选择；获取模型和测试连接均使用已保存的服务地址与 API Key，不会自动保存。
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
                  {busy.llmtest ? "测试中…" : "测试连接"}
                </button>
              ) : null}
              <button
                disabled={savingSec === section}
                onClick={() => doSave(section)}
              >
                {savingSec === section ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </Page>
  );
}
