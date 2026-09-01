import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ModelsResponse } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Chip, Empty, useBusy } from "../ui/badges";

export default function ModelsPage() {
  const [data, setData] = useState<ModelsResponse | null>(null);
  const [fileName, setFileName] = useState("");
  const [regFile, setRegFile] = useState<string | null>(null);
  const [regName, setRegName] = useState("");
  const [regConf, setRegConf] = useState("");
  const [regEnabled, setRegEnabled] = useState(false);
  const [regOpen, setRegOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [confVals, setConfVals] = useState<Record<string, string>>({});
  const toast = useToast();
  const confirm = useConfirm();
  const { busy, wrap } = useBusy();

  const refresh = useCallback(async () => {
    setData(await api<ModelsResponse>("/api/models"));
  }, []);

  const refreshLater = (ms: number) => setTimeout(refresh, ms);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const upload = wrap("up", async () => {
    const input = fileRef.current;
    if (!input || !input.files?.length) {
      toast("请先选择 .pt 文件", false);
      return;
    }
    if (input.files[0].size > 200 * 1024 * 1024) {
      toast("文件超过 200MB 上限", false);
      return;
    }
    const fd = new FormData();
    fd.append("file", input.files[0]);
    try {
      await api("/api/models/files", { method: "POST", body: fd });
      toast("上传成功，开始后台校验");
      if (input) input.value = "";
      setFileName("");
      refresh();
      refreshLater(2500);
    } catch (e) {
      toast((e as Error).message || "上传失败", false);
    }
  });

  const openReg = (file: string) => {
    setRegFile(file);
    setRegName("");
    setRegConf("");
    setRegEnabled(false);
    setRegOpen(true);
  };

  const doRegister = wrap("reg", async () => {
    try {
      const body = {
        name: regName.trim(),
        file: regFile,
        enabled: regEnabled,
        confidence_override: regConf === "" ? null : +regConf,
      };
      await api("/api/models", { method: "POST", body });
      toast("已注册" + (body.enabled ? "，后台加载中" : "（未启用）"));
      setRegOpen(false);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const modelRevision = (name: string) =>
    data?.models.find((m) => m.name === name)?.revision;

  const saveThreshold = (name: string, raw: string) =>
    wrap(`th-${name}`, async () => {
      const v = parseFloat(raw);
      if (raw === "" || !Number.isFinite(v) || v < 0 || v > 1) {
        toast("conf 需为 0~1 之间的数字", false);
        return;
      }
      try {
        await api(`/api/models/${encodeURIComponent(name)}`, {
          method: "PUT",
          body: { confidence_override: v, expected_revision: modelRevision(name) },
        });
        toast("阈值已保存并热生效");
      } catch (e) {
        if ((e as { status?: number }).status === 409) {
          toast("配置已被其他请求修改，请刷新后重试", false);
          await refresh();
        } else {
          toast((e as Error).message, false);
        }
      }
      refresh();
    })();

  const toggle = (name: string, enable: boolean) =>
    wrap(`tg-${name}`, async () => {
      try {
        await api(`/api/models/${encodeURIComponent(name)}`, {
          method: "PUT",
          body: { enabled: enable, expected_revision: modelRevision(name) },
        });
        toast(enable ? "已启用，后台加载中" : "已停用");
      } catch (e) {
        if ((e as { status?: number }).status === 409) {
          toast("配置已被其他请求修改，请刷新后重试", false);
          await refresh();
        } else {
          toast((e as Error).message, false);
        }
      }
      refresh();
    })();

  const reload = (name: string) =>
    wrap(`rl-${name}`, async () => {
      try {
        await api(`/api/models/${encodeURIComponent(name)}/reload`, { method: "POST" });
        toast("重载中…");
      } catch (e) {
        toast((e as Error).message, false);
      }
      refreshLater(1500);
    })();

  const unregister = async (name: string) => {
    if (!(await confirm(`注销模型 ${name}？模型文件不会被删除。`))) return;
    try {
      const revision = modelRevision(name);
      const suffix = revision == null ? "" : `?expected_revision=${revision}`;
      await api(`/api/models/${encodeURIComponent(name)}${suffix}`, { method: "DELETE" });
      toast("已注销");
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

  const delFile = async (file: string) => {
    if (!(await confirm(`删除模型文件 ${file}？`))) return;
    try {
      await api(`/api/models/files/${encodeURIComponent(file)}`, { method: "DELETE" });
      toast("已删除");
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  };

  const models = data?.models || [];
  const files = data?.files || [];

  return (
    <Page
      title="模型管理"
      subtitle="上传 .pt → 后台校验 → 注册 → 启用热加载；阈值改动即时生效"
    >
      <div className="grid row2">
        <div className="card">
          <div className="card-title">已注册模型</div>
          {models.length ? (
            models.map((m) => (
              <div className="model-card" key={m.name}>
                <div className="head">
                  <b>{m.name}</b>
                  {m.loaded ? (
                    <Chip text={`已加载 · ${m.device || "?"}`} color="green" />
                  ) : m.config_enabled ? (
                    <Chip text="加载中 / 失败" color="yellow" />
                  ) : (
                    <Chip text="已停用" />
                  )}
                </div>
                <div className="meta mono">
                  {m.path} {m.file_exists ? null : <Chip text="文件缺失" color="red" />}
                </div>
                {m.confidence_override != null ? (
                  <div className="meta">
                    置信度覆盖：<span className="mono">{m.confidence_override}</span>
                  </div>
                ) : null}
                <div className="meta">
                  类别 {Object.keys(m.classes || {}).length}：
                  {Object.entries(m.classes || {}).slice(0, 12).map(([id, name]) => (
                    <Chip key={id} text={`${id}:${name}`} />
                  )) || "—"}
                  {Object.keys(m.classes || {}).length > 12 ? (
                    <span className="muted">…</span>
                  ) : null}
                </div>
                <div className="ops">
                  <label style={{ color: "var(--muted)" }}>conf</label>
                  <input
                    className="mini"
                    style={{ width: 76 }}
                    type="number"
                    step={0.05}
                    min={0}
                    max={1}
                    placeholder={String(m.confidence ?? "")}
                    value={confVals[m.name] ?? ""}
                    onChange={(e) => setConfVals((v) => ({ ...v, [m.name]: e.target.value }))}
                  />
                  <button
                    className="mini"
                    disabled={busy[`th-${m.name}`]}
                    onClick={() => saveThreshold(m.name, confVals[m.name] ?? "")}
                  >
                    保存阈值
                  </button>
                  <button
                    className="mini ghost"
                    disabled={busy[`tg-${m.name}`]}
                    onClick={() => toggle(m.name, !m.config_enabled)}
                  >
                    {m.config_enabled ? "停用" : "启用"}
                  </button>
                  <button
                    className="mini ghost"
                    disabled={busy[`rl-${m.name}`]}
                    onClick={() => reload(m.name)}
                  >
                    重载
                  </button>
                  <button className="mini danger" onClick={() => unregister(m.name)}>
                    注销
                  </button>
                </div>
              </div>
            ))
          ) : (
            <Empty>尚未注册模型</Empty>
          )}
        </div>
        <div className="card">
          <div className="card-title">模型文件</div>
          <div className="banner" style={{ marginBottom: 12 }}>
            <Icon name="alert-triangle" size={14} />
            <span>.pt 为可执行 pickle，仅导入可信来源的模型；上限 200MB</span>
          </div>
          <div className="toolbar" style={{ marginBottom: 14 }}>
            <label
              className="mini ghost"
              style={{ padding: "6px 12px", cursor: "pointer", flexShrink: 0 }}
            >
              选择 .pt 文件
              <input
                ref={fileRef}
                type="file"
                accept=".pt"
                style={{ display: "none" }}
                onChange={(e) => setFileName(e.target.files?.[0]?.name || "")}
              />
            </label>
            <span className="muted" style={{ fontSize: 12, flex: 1 }}>
              {fileName || "未选择文件"}
            </span>
            <button disabled={busy.up} onClick={upload}>
              上传
            </button>
          </div>
          <div className="table-wrap">
            <table style={{ minWidth: 0 }}>
              <tbody>
                {files.length ? (
                  files.map((f) => (
                    <tr key={f.file}>
                      <td className="mono">
                        {f.file}
                        <div className="muted" style={{ fontSize: 11 }}>
                          {f.size_mb} MB
                        </div>
                      </td>
                      <td>
                        {f.validation.status === "有效" ? (
                          <Chip
                            text={`有效 · ${Object.keys(f.validation.classes || {}).length} 类`}
                            color="green"
                          />
                        ) : f.validation.status === "无效" ? (
                          <Chip text="无效" color="red" />
                        ) : (
                          <Chip text={f.validation.status} color="yellow" />
                        )}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {f.registered_as ? (
                          <Chip text="已注册" color="blue" />
                        ) : (
                          <>
                            <button className="mini" onClick={() => openReg(f.file)}>
                              注册
                            </button>
                            <button className="mini danger" onClick={() => delFile(f.file)}>
                              删除
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td>
                      <Empty>models/ 目录暂无 .pt 文件</Empty>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {regOpen && (
        <Modal
          title="注册为模型实例"
          width={460}
          onClose={() => setRegOpen(false)}
          footer={
            <>
              <button className="ghost" onClick={() => setRegOpen(false)}>
                取消
              </button>
              <button disabled={busy.reg} onClick={doRegister}>
                注册
              </button>
            </>
          }
        >
          <label>模型名称（字母 / 数字 / 下划线 / 连字符）</label>
          <input
            className="w320"
            placeholder="yolov8-ppe-v2"
            value={regName}
            onChange={(e) => setRegName(e.target.value)}
          />
          <p className="muted" style={{ marginTop: 8 }}>
            {(() => {
              const f = files.find((x) => x.file === regFile)?.validation;
              return (
                `文件：${regFile} · 校验：${f?.status ?? "未知"}` +
                (f?.classes ? ` · ${Object.keys(f.classes).length} 类` : "") +
                (f?.status !== "有效" ? "（建议等待校验通过后再启用）" : "")
              );
            })()}
          </p>
          <label>置信度覆盖（可选，0 ~ 1）</label>
          <input
            className="w240"
            type="number"
            step={0.05}
            min={0}
            max={1}
            value={regConf}
            onChange={(e) => setRegConf(e.target.value)}
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
              checked={regEnabled}
              onChange={(e) => setRegEnabled(e.target.checked)}
            />{" "}
            注册后立即启用（热加载）
          </label>
        </Modal>
      )}
    </Page>
  );
}
