import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { DatasetInfo, ModelsResponse, TrainRun, TrainStatus } from "../api/types";
import { Page } from "../layout/Page";
import { Modal } from "../ui/Modal";
import { useToast } from "../ui/Toast";
import { Chip, Empty, useBusy } from "../ui/badges";

const STATE_CHIPS: Record<string, { text: string; color: string }> = {
  running: { text: "训练中", color: "blue" },
  completed: { text: "已完成", color: "green" },
  failed: { text: "失败", color: "red" },
};

function shortPath(p: string) {
  const parts = String(p).split("/");
  return parts.length > 2 ? "…/" + parts.slice(-2).join("/") : p;
}

export default function TrainPage() {
  const [dsList, setDsList] = useState<DatasetInfo[]>([]);
  const [modelFiles, setModelFiles] = useState<string[]>([]);
  const [runs, setRuns] = useState<TrainRun[]>([]);
  const [status, setStatus] = useState<TrainStatus | null>(null);
  const [regRun, setRegRun] = useState<string | null>(null);
  const [regName, setRegName] = useState("");
  const [form, setForm] = useState({
    dataset: "",
    base_model: "",
    epochs: "50",
    imgsz: "640",
    batch: "8",
    device: "auto",
    name: "",
  });
  const toast = useToast();
  const { busy, wrap } = useBusy();

  const loadRuns = useCallback(async () => {
    const r = await api<{ runs: TrainRun[] }>("/api/train/runs");
    setRuns(r.runs);
  }, []);

  useEffect(() => {
    Promise.all([
      api<{ datasets: DatasetInfo[] }>("/api/datasets"),
      api<ModelsResponse>("/api/models"),
      api<{ runs: TrainRun[] }>("/api/train/runs"),
    ]).then(([ds, models, runs]) => {
      setDsList(ds.datasets);
      setModelFiles(models.files.map((f) => f.file));
      setRuns(runs.runs);
      setForm((f) => ({
        ...f,
        base_model: f.base_model || (models.files[0]?.file || "yolov8n.pt"),
      }));
    });
  }, []);

  const pollStatus = useCallback(async () => {
    try {
      setStatus(await api<TrainStatus>("/api/train/status"));
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    pollStatus();
    const t = setInterval(pollStatus, 3000);
    return () => clearInterval(t);
  }, [pollStatus]);

  const start = wrap("start", async () => {
    try {
      const r = await api<{ name: string; pid: number }>("/api/train/start", {
        method: "POST",
        body: {
          dataset: form.dataset,
          base_model: form.base_model,
          epochs: +form.epochs,
          imgsz: +form.imgsz,
          batch: +form.batch,
          device: form.device,
          name: form.name.trim(),
        },
      });
      toast(`训练已启动：${r.name}（PID ${r.pid}）`);
      pollStatus();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const stop = wrap("stop", async () => {
    try {
      const r = await api<{ stopped: boolean }>("/api/train/stop", { method: "POST" });
      toast(r.stopped ? "已发送停止信号" : "当前没有运行中的训练");
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const doRegister = wrap("reg", async () => {
    if (!regRun) return;
    try {
      await api("/api/train/register", {
        method: "POST",
        body: { run: regRun, model_name: regName.trim() },
      });
      toast("已复制 best.pt 并注册（默认停用，到模型管理页启用）");
      setRegRun(null);
      loadRuns();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const openRegister = (run: string) => {
    setRegRun(run);
    setRegName(`trained-${run}`);
  };

  const st = status;
  const pct =
    st?.epoch && st?.epochs_total
      ? Math.min(100, Math.round((st.epoch / st.epochs_total) * 100))
      : 0;
  const chip = st ? STATE_CHIPS[st.state] || { text: st.state, color: "" } : null;

  return (
    <Page
      title="模型训练"
      subtitle="子进程训练，崩溃不影响检测与面板；每个 epoch 解析一次进度"
    >
      <div className="grid row2">
        <div className="card">
          <div className="card-title">启动训练</div>
          <label>数据集（YOLO 格式，来自「数据集」页）</label>
          <select
            style={{ width: "100%" }}
            value={form.dataset}
            onChange={(e) => setForm({ ...form, dataset: e.target.value })}
          >
            {dsList.length ? (
              dsList.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}（{d.images} 张）
                </option>
              ))
            ) : (
              <option value="">无数据集，请先创建</option>
            )}
          </select>
          <label>基础模型（models/ 目录下的 .pt，或官方名）</label>
          <select
            style={{ width: "100%" }}
            value={form.base_model}
            onChange={(e) => setForm({ ...form, base_model: e.target.value })}
          >
            {modelFiles.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
            <option value="yolov8n.pt">yolov8n.pt（官方预训练）</option>
          </select>
          <div className="form-grid">
            <div>
              <label>轮数 epochs</label>
              <input
                type="number"
                value={form.epochs}
                min={1}
                onChange={(e) => setForm({ ...form, epochs: e.target.value })}
              />
            </div>
            <div>
              <label>图片尺寸 imgsz</label>
              <input
                type="number"
                value={form.imgsz}
                min={32}
                onChange={(e) => setForm({ ...form, imgsz: e.target.value })}
              />
            </div>
            <div>
              <label>batch</label>
              <input
                type="number"
                value={form.batch}
                min={1}
                onChange={(e) => setForm({ ...form, batch: e.target.value })}
              />
            </div>
            <div>
              <label>设备 device</label>
              <select
                style={{ width: "100%" }}
                value={form.device}
                onChange={(e) => setForm({ ...form, device: e.target.value })}
              >
                <option value="auto">auto</option>
                <option value="cpu">cpu</option>
                <option value="mps">mps</option>
                <option value="cuda:0">cuda:0</option>
              </select>
            </div>
          </div>
          <label>任务名（默认 数据集_epochs）</label>
          <input
            className="w320"
            placeholder="site_ppe_v1_50ep"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <div style={{ marginTop: 16, display: "flex", gap: 10 }}>
            <button disabled={busy.start} onClick={start}>
              ▶ 开始训练
            </button>
            <button className="danger" disabled={busy.stop} onClick={stop}>
              ■ 停止
            </button>
          </div>
          <p className="muted" style={{ marginTop: 12 }}>
            提示：demo 数据集验证集=训练集，mAP 仅作参考；一次只跑一个训练任务。
          </p>
        </div>

        <div className="stack" style={{ height: "100%", minWidth: 0 }}>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">
              训练状态{" "}
              <span className="muted">{st?.name ? `· ${st.name}` : ""}</span>
            </div>
            {!st || st.state === "idle" || !st.state ? (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  minHeight: 200,
                }}
              >
                <p className="muted">尚未启动训练</p>
              </div>
            ) : (
              <div>
                <div
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "center",
                    marginBottom: 10,
                  }}
                >
                  <Chip text={chip!.text} color={chip!.color} />
                  <span className="muted">
                    epoch {st.epoch ?? 0}/{st.epochs_total ?? "?"}
                  </span>
                  {st.mAP50 != null ? (
                    <span className="muted">
                      mAP50 <b style={{ color: "var(--text)" }}>{st.mAP50}</b>
                    </span>
                  ) : null}
                  {st.mAP50_95 != null ? (
                    <span className="muted">
                      mAP50-95 <b style={{ color: "var(--text)" }}>{st.mAP50_95}</b>
                    </span>
                  ) : null}
                </div>
                <div
                  style={{
                    height: 8,
                    background: "var(--surface-3)",
                    borderRadius: 6,
                    overflow: "hidden",
                    marginBottom: 10,
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: `${pct}%`,
                      borderRadius: 6,
                      background: "linear-gradient(90deg,var(--accent),var(--accent-2))",
                      transition: "width .5s",
                    }}
                  />
                </div>
                {st.best_path ? (
                  <div className="muted" style={{ marginBottom: 8 }}>
                    best.pt：{st.state === "completed" ? (
                      <button className="mini" onClick={() => openRegister(st.name || "")}>
                        注册为模型
                      </button>
                    ) : (
                      st.best_path
                    )}
                  </div>
                ) : null}
              </div>
            )}
          </div>

          {/* minWidth:0 —— 防止 flex/grid 子项被表格 min-content 撑出容器 */}
          <div className="card" style={{ minWidth: 0 }}>
            <div className="card-title">
              历史产物 <span className="muted">best.pt 可注册为模型</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>best.pt</th>
                    <th>大小</th>
                    <th style={{ textAlign: "right" }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.length ? (
                    runs.map((r) => (
                      <tr key={r.name}>
                        <td className="mono">{r.name}</td>
                        <td
                          className="mono"
                          style={{
                            fontSize: 11,
                            maxWidth: 150,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={r.best}
                        >
                          {shortPath(r.best)}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }}>{r.size_mb} MB</td>
                        <td style={{ textAlign: "right" }}>
                          <button className="mini" onClick={() => openRegister(r.name)}>
                            注册为模型
                          </button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={4}>
                        <Empty>暂无训练产物</Empty>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-title">
          日志（尾部）<span className="muted">训练子进程 stdout</span>
        </div>
        <pre className="log" style={{ maxHeight: 240 }}>
          {st?.log_tail?.length ? st.log_tail.join("\n") : "—"}
        </pre>
      </div>

      {regRun && (
        <Modal
          title="注册为模型"
          width={440}
          onClose={() => setRegRun(null)}
          footer={
            <>
              <button className="ghost" onClick={() => setRegRun(null)}>
                取消
              </button>
              <button disabled={busy.reg} onClick={doRegister}>
                注册
              </button>
            </>
          }
        >
          <label>模型名称（字母/数字/连字符）</label>
          <input
            style={{ width: "100%" }}
            value={regName}
            onChange={(e) => setRegName(e.target.value)}
          />
        </Modal>
      )}
    </Page>
  );
}
