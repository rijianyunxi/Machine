import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Camera, CameraTestResult, RuleEntry } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { usePolling } from "../hooks/usePolling";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Chip, ConnectedBadge, Empty, useBusy } from "../ui/badges";

/* RTSP 分段字段（编辑态的表单值） */
interface CamForm {
  id: string;
  name: string;
  enabled: boolean;
  rules: number[];
  mode: "build" | "raw";
  vendor: "dahua" | "hikvision" | "generic";
  ip: string;
  port: string;
  user: string;
  pass: string;
  ch: string;
  stream: "0" | "1";
  path: string;
  rawUrl: string;
}

const EMPTY_FORM: CamForm = {
  id: "",
  name: "",
  enabled: true,
  rules: [],
  mode: "build",
  vendor: "dahua",
  ip: "",
  port: "554",
  user: "",
  pass: "",
  ch: "1",
  stream: "1",
  path: "",
  rawUrl: "",
};

/* 由（可能脱敏的）完整 URL 反推分段字段；解析失败返回 null */
function parseRtsp(u: string) {
  const m =
    /^rtsp:\/\/(?:([^:\/@]+)(?::([^@]*))?@)?([^:\/@]+)(?::(\d+))?(\/[^\s]*)?$/.exec(
      u || "",
    );
  if (!m) return null;
  const user = m[1] ? decodeURIComponent(m[1]) : "";
  const pass = m[2] ? decodeURIComponent(m[2]) : "";
  const base = {
    ip: m[3] || "",
    port: m[4] || "554",
    user,
    pass: pass && pass !== "****" ? pass : "",
  };
  const path = m[5] || "";
  const dm = /cam\/realmonitor\?channel=(\d+)&subtype=(\d+)/.exec(path);
  const hm = /Streaming\/Channels\/(\d+)/.exec(path);
  if (dm) {
    return {
      ...base,
      vendor: "dahua" as const,
      ch: dm[1],
      stream: (dm[2] === "0" ? "0" : "1") as "0" | "1",
      path: "",
    };
  }
  if (hm) {
    const code = +hm[1];
    return {
      ...base,
      vendor: "hikvision" as const,
      ch: String(Math.max(1, Math.floor(code / 100))),
      stream: (code % 10 === 2 ? "1" : "0") as "0" | "1",
      path: "",
    };
  }
  return { ...base, vendor: "generic" as const, ch: "1", stream: "1" as const, path };
}

/* 分段字段 → RTSP；编辑且密码留空时用 __KEEP__ 占位（后端还原） */
function buildRtsp(f: CamForm, editing: boolean): string {
  if (!f.ip) return "";
  const port = f.port || "554";
  let auth = "";
  if (f.user) {
    auth =
      f.user +
      ":" +
      (f.pass
        ? encodeURIComponent(f.pass)
        : editing
          ? "__KEEP__"
          : "") +
      "@";
    if (!f.pass && !editing) auth = f.user + "@"; // 新增且无密码：无认证段
  }
  const ch = Math.max(1, +f.ch || 1);
  const sub = f.stream;
  let path = "";
  if (f.vendor === "dahua") path = `/cam/realmonitor?channel=${ch}&subtype=${sub}`;
  else if (f.vendor === "hikvision")
    path = `/Streaming/Channels/${ch * 100 + (sub === "0" ? 1 : 2)}`;
  else path = f.path || "/";
  return `rtsp://${auth}${f.ip}:${port}${path}`;
}

export default function CamerasPage() {
  const [cams, setCams] = useState<Camera[] | null>(null);
  const [rules, setRules] = useState<RuleEntry[]>([]);
  const [tick, setTick] = useState(() => Date.now());
  const [imgErr, setImgErr] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<CamForm>(EMPTY_FORM);
  const [modalOpen, setModalOpen] = useState(false);
  const [passPh, setPassPh] = useState("留空保持原密码");
  const [rawPh, setRawPh] = useState("rtsp://用户名:密码@IP:554/... 或 test_videos/demo.mp4");
  const [testResult, setTestResult] = useState<React.ReactNode>(null);
  const [preview, setPreview] = useState<{ id: string; name: string } | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const { busy, wrap } = useBusy();

  const refresh = useCallback(async () => {
    const r = await api<{ cameras: Camera[] }>("/api/cameras");
    setCams(r.cameras);
    setTick(Date.now());
  }, []);

  useEffect(() => {
    api<{ rules: RuleEntry[] }>("/api/rules").then((r) => setRules(r.rules));
  }, []);
  usePolling(refresh, 5000);

  const set = (patch: Partial<CamForm>) => setForm((f) => ({ ...f, ...patch }));

  const openEdit = async (id: string | null) => {
    setEditing(id);
    setTestResult(null);
    setPassPh("留空保持原密码");
    setRawPh("rtsp://用户名:密码@IP:554/... 或 test_videos/demo.mp4");
    const { cameras } = await api<{ cameras: Camera[] }>("/api/cameras");
    const cam = cameras.find((c) => c.id === id);
    let base: CamForm = {
      ...EMPTY_FORM,
      id: cam?.id || "",
      name: cam?.name || "",
      enabled: cam ? cam.enabled : true,
      rules: cam ? cam.rules || [] : [],
    };
    const raw = cam?.url || "";
    const masked = raw.includes("****");
    if (id) {
      const parsed = parseRtsp(raw);
      if (parsed) {
        base = { ...base, ...parsed };
        if (masked) setPassPh("已保存，留空 = 保持原密码");
      } else {
        // 解析不出（如本地视频路径）才切到直接粘贴
        base = { ...base, mode: "raw", rawUrl: masked ? "" : raw };
        if (masked) setRawPh("已保存（脱敏显示，重新填写完整地址）");
      }
    }
    setForm(base);
    setModalOpen(true);
  };

  const built = buildRtsp(form, !!editing);
  const currentUrl = () => (form.mode === "raw" ? form.rawUrl.trim() : built);

  const switchMode = (mode: "build" | "raw") => {
    if (mode === "raw") {
      set({ mode, rawUrl: built || "" });
      setTestResult(null);
      return;
    }
    const parsed = parseRtsp(form.rawUrl.trim());
    if (parsed) set({ mode, ...parsed });
    else set({ mode });
    setTestResult(null);
  };

  const testPlay = wrap("test", async () => {
    const url = currentUrl();
    if (!url) {
      toast("请先填写地址", false);
      return;
    }
    setTestResult(<Chip text="检测中…（最长约 8 秒）" color="yellow" />);
    try {
      const r = await api<CameraTestResult>("/api/cameras/test", {
        method: "POST",
        body: { url, camera_id: editing || "" },
      });
      setTestResult(
        r.ok ? (
          <Chip
            text={`可播放 · ${r.width}×${r.height} @ ${r.fps}fps · 首帧 ${r.latency_ms}ms`}
            color="green"
          />
        ) : (
          <Chip text={r.error} color="red" />
        ),
      );
    } catch (e) {
      setTestResult(<Chip text={(e as Error).message} color="red" />);
    }
  });

  const saveCam = wrap("save", async () => {
    const url = currentUrl();
    if (!url) {
      toast(form.mode === "raw" ? "请填写地址" : "请填写监控 IP", false);
      return;
    }
    const body = {
      id: form.id.trim(),
      name: form.name.trim(),
      enabled: form.enabled,
      rules: form.rules,
      rtsp_url: url,
    };
    try {
      if (editing)
        await api(`/api/cameras/${encodeURIComponent(editing)}`, { method: "PUT", body });
      else await api("/api/cameras", { method: "POST", body });
      toast(editing ? "已保存并热生效" : "已新增并热生效");
      setModalOpen(false);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const restartCam = (id: string) => {
    void wrap(`re-${id}`, async () => {
      try {
        await api(`/api/cameras/${encodeURIComponent(id)}/restart`, { method: "POST" });
        toast("已触发重连");
      } catch (e) {
        toast((e as Error).message, false);
      }
    })();
  };

  const delCam = async (id: string) => {
    if (
      !(await confirm(
        `确认删除监控 ${id}？将同时从 cameras.yaml 移除，历史告警保留。`,
      ))
    )
      return;
    try {
      await api(`/api/cameras/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("已删除");
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  };

  const toggleRule = (rid: number) =>
    set({
      rules: form.rules.includes(rid)
        ? form.rules.filter((x) => x !== rid)
        : [...form.rules, rid],
    });

  return (
    <Page
      title="监控管理"
      subtitle="新增 / 编辑即时热生效；分段填写自动组装 RTSP，支持测试播放"
      actions={
        <button onClick={() => openEdit(null)} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Icon name="plus" size={13} /> 新增监控
        </button>
      }
    >
      <div className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>状态</th>
                <th>预览</th>
                <th>ID / 名称</th>
                <th>地址</th>
                <th>规则</th>
                <th>已采帧</th>
                <th>最后帧龄</th>
                <th style={{ textAlign: "right" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {cams === null ? (
                <tr>
                  <td colSpan={8}>
                    <Empty>加载中…</Empty>
                  </td>
                </tr>
              ) : cams.length ? (
                cams.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <ConnectedBadge cam={c} />
                    </td>
                    <td style={{ width: 150 }}>
                      {c.enabled && c.connected && !imgErr[c.id] ? (
                        <img
                          className="thumb"
                          style={{ cursor: "zoom-in" }}
                          src={`/api/cameras/${encodeURIComponent(c.id)}/frame.jpg?t=${tick}`}
                          onClick={() => setPreview({ id: c.id, name: c.name })}
                          onError={() => setImgErr((m) => ({ ...m, [c.id]: true }))}
                          onLoad={() => setImgErr((m) => ({ ...m, [c.id]: false }))}
                          alt={c.name}
                        />
                      ) : (
                        <div className="thumb-fallback">
                          {imgErr[c.id] && c.connected ? "暂无画面" : c.enabled ? "无画面" : "已停用"}
                        </div>
                      )}
                    </td>
                    <td>
                      <b>{c.id}</b>
                      <div className="muted" style={{ fontSize: 12 }}>
                        {c.name}
                      </div>
                    </td>
                    <td
                      className="mono"
                      style={{
                        maxWidth: 240,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={c.url}
                    >
                      {c.url}
                    </td>
                    <td>
                      {(c.rules || []).length ? (
                        (c.rules || []).map((r) => (
                          <Chip key={r} text={"R" + String(r).padStart(2, "0")} color="blue" />
                        ))
                      ) : (
                        <span className="muted">未分配</span>
                      )}
                    </td>
                    <td className="mono">{c.frames_captured ?? "—"}</td>
                    <td className="mono">{c.frame_age != null ? c.frame_age + "s" : "—"}</td>
                    <td className="actions" style={{ textAlign: "right" }}>
                      <button className="mini ghost" onClick={() => openEdit(c.id)}>
                        编辑
                      </button>
                      <button
                        className="mini ghost"
                        disabled={busy[`re-${c.id}`]}
                        onClick={() => restartCam(c.id)}
                      >
                        重连
                      </button>
                      <button className="mini danger" onClick={() => delCam(c.id)}>
                        删除
                      </button>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8}>
                    <Empty>尚未配置监控</Empty>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {modalOpen && (
        <Modal
          title={editing ? `编辑监控 · ${editing}` : "新增监控"}
          width={620}
          onClose={() => setModalOpen(false)}
          footer={
            <>
              <button className="ghost" disabled={busy.test} onClick={testPlay}>
                <Icon name="play" size={12} /> 测试播放
              </button>
              <span style={{ flex: 1 }} />
              <button className="ghost" onClick={() => setModalOpen(false)}>
                取消
              </button>
              <button id="save-btn" disabled={busy.save} onClick={saveCam}>
                保存
              </button>
            </>
          }
        >
          <div className="form-grid">
            <div>
              <label>监控 ID（唯一，保存后不可改）</label>
              <input
                className="w240"
                placeholder="CAM_001"
                disabled={!!editing}
                value={form.id}
                onChange={(e) => set({ id: e.target.value })}
              />
            </div>
            <div>
              <label>名称</label>
              <input
                className="w240"
                placeholder="车间北门"
                value={form.name}
                onChange={(e) => set({ name: e.target.value })}
              />
            </div>
          </div>

          <div className="toolbar" style={{ marginTop: 14 }}>
            <label style={{ margin: 0, color: "var(--text)", fontSize: 12 }}>连接方式</label>
            <select
              value={form.mode}
              onChange={(e) => switchMode(e.target.value as "build" | "raw")}
            >
              <option value="build">分段填写（自动组装）</option>
              <option value="raw">直接粘贴完整地址</option>
            </select>
          </div>

          {form.mode === "build" ? (
            <div id="build-box">
              <div className="form-grid" style={{ marginTop: 10 }}>
                <div>
                  <label>品牌 / 地址格式</label>
                  <select
                    style={{ width: "100%" }}
                    value={form.vendor}
                    onChange={(e) => set({ vendor: e.target.value as CamForm["vendor"] })}
                  >
                    <option value="dahua">大华（/cam/realmonitor）</option>
                    <option value="hikvision">海康（/Streaming/Channels）</option>
                    <option value="generic">通用（自定义路径）</option>
                  </select>
                </div>
              </div>
              <div className="form-grid" style={{ marginTop: 2 }}>
                <div>
                  <label>IP 地址</label>
                  <input placeholder="192.168.1.108" value={form.ip} onChange={(e) => set({ ip: e.target.value })} />
                </div>
                <div>
                  <label>端口</label>
                  <input value={form.port} onChange={(e) => set({ port: e.target.value })} />
                </div>
                <div>
                  <label>用户名</label>
                  <input placeholder="admin" value={form.user} onChange={(e) => set({ user: e.target.value })} />
                </div>
                <div>
                  <label>密码（编辑时留空 = 保持原密码）</label>
                  <input
                    type="password"
                    placeholder={passPh}
                    value={form.pass}
                    onChange={(e) => set({ pass: e.target.value })}
                  />
                </div>
                <div>
                  <label>通道号</label>
                  <input
                    type="number"
                    min={1}
                    value={form.ch}
                    onChange={(e) => set({ ch: e.target.value })}
                  />
                </div>
                <div>
                  <label>码流</label>
                  <select
                    style={{ width: "100%" }}
                    value={form.stream}
                    onChange={(e) => set({ stream: e.target.value as "0" | "1" })}
                  >
                    <option value="1">子码流（检测推荐）</option>
                    <option value="0">主码流</option>
                  </select>
                </div>
              </div>
              {form.vendor === "generic" ? (
                <div id="f-generic-wrap">
                  <label>自定义路径（含 / 开头）</label>
                  <input
                    style={{ width: "100%" }}
                    placeholder="/live/ch1"
                    value={form.path}
                    onChange={(e) => set({ path: e.target.value })}
                  />
                </div>
              ) : null}
              <label style={{ marginTop: 12 }}>组装结果</label>
              <div
                className="mono"
                style={{
                  background: "var(--surface-2)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  padding: "8px 10px",
                  wordBreak: "break-all",
                  fontSize: 12,
                  minHeight: 33,
                }}
              >
                {built || "填写 IP 后自动组装"}
              </div>
            </div>
          ) : (
            <div id="raw-box">
              <label style={{ marginTop: 10 }}>RTSP 地址 / 本地视频路径</label>
              <input
                style={{ width: "100%" }}
                placeholder={rawPh}
                value={form.rawUrl}
                onChange={(e) => set({ rawUrl: e.target.value })}
              />
            </div>
          )}

          <label>启用规则（多选）</label>
          <div className="inline-checks" id="f-rules">
            {rules.length ? (
              rules.map((x) => (
                <label key={x.id}>
                  <input
                    type="checkbox"
                    checked={form.rules.includes(x.id)}
                    onChange={() => toggleRule(x.id)}
                  />
                  <span className="chip blue plain">R{String(x.id).padStart(2, "0")}</span>
                  {x.name}
                </label>
              ))
            ) : (
              <span className="muted">尚无规则，请先到「规则配置」新建</span>
            )}
          </div>
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
              onChange={(e) => set({ enabled: e.target.checked })}
            />{" "}
            启用该监控
          </label>
          <div id="test-result" style={{ marginTop: 12 }}>
            {testResult}
          </div>
        </Modal>
      )}

      {preview && (
        <Modal
          title={`实时预览 · ${preview.name || preview.id}`}
          width={780}
          onClose={() => setPreview(null)}
          footer={
            <>
              <span className="muted" style={{ marginRight: "auto" }}>
                MJPEG 实时流 · 约每路 2fps
              </span>
              <button className="ghost" onClick={() => setPreview(null)}>
                关闭
              </button>
            </>
          }
        >
          <img
            src={`/api/cameras/${encodeURIComponent(preview.id)}/stream.mjpg`}
            alt="实时预览"
            style={{
              width: "100%",
              borderRadius: 10,
              display: "block",
              background: "var(--surface-2)",
              lineHeight: 0,
            }}
          />
        </Modal>
      )}
    </Page>
  );
}
