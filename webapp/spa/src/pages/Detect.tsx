import { Select, type SelectHandle } from "../ui/Select";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  Camera,
  DetectHistoryItem,
  DetectResult,
  ModelsResponse,
} from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { useToast } from "../ui/Toast";
import { Empty, useBusy } from "../ui/badges";
import { useLightbox } from "../ui/Lightbox";

export default function DetectPage() {
  const [cams, setCams] = useState<Camera[]>([]);
  const [loadedModels, setLoadedModels] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [conf, setConf] = useState("");
  const [result, setResult] = useState<DetectResult | null>(null);
  const [json, setJson] = useState("等待检测…");
  const [history, setHistory] = useState<DetectHistoryItem[] | null>(null);
  const [imgName, setImgName] = useState("");
  const [imgTick, setImgTick] = useState(() => Date.now());
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<SelectHandle>(null);
  const toast = useToast();
  const { showImage } = useLightbox();
  const { busy, wrap } = useBusy();

  const loadHistory = useCallback(async () => {
    const r = await api<{ results: DetectHistoryItem[] }>("/api/detect/test/history");
    setHistory(r.results);
  }, []);

  useEffect(() => {
    api<{ cameras: Camera[] }>("/api/cameras").then((r) => setCams(r.cameras.filter((c) => c.enabled)));
    api<ModelsResponse>("/api/models").then((r) => {
      const names = r.models.filter((m) => m.loaded).map((m) => m.name);
      setLoadedModels(names);
      setPicked(names); // 旧版默认全选
    });
    loadHistory();
  }, [loadHistory]);

  const pickedModels = () => picked.join(",");

  const runTest = wrap("run", async () => {
    const input = fileRef.current;
    if (!input?.files?.length) {
      toast("请先选择图片", false);
      return;
    }
    const fd = new FormData();
    fd.append("image", input.files[0]);
    fd.append("models", pickedModels());
    if (conf !== "") fd.append("conf", conf);
    try {
      const data = await api<DetectResult>("/api/detect/test", { method: "POST", body: fd });
      handle(data);
    } catch (e) {
      toast((e as Error).message || "检测失败", false);
    }
  });

  const runCamFrame = wrap("cam", async () => {
    const cam = camRef.current?.value;
    if (!cam) {
      toast("无可用监控", false);
      return;
    }
    const qs = new URLSearchParams({ models: pickedModels() });
    if (conf !== "") qs.set("conf", conf);
    try {
      const data = await api<DetectResult>(
        `/api/detect/test/camera/${encodeURIComponent(cam)}?${qs}`,
        { method: "POST" },
      );
      handle(data);
    } catch (e) {
      toast((e as Error).message || "检测失败", false);
    }
  });

  const handle = (data: DetectResult) => {
    setResult(data);
    setImgTick(Date.now());
    setJson(
      JSON.stringify(
        { latency_ms: data.latency_ms, models: data.models, detections: data.detections },
        null,
        2,
      ),
    );
    toast(`完成：${data.detections.length} 个目标 · ${data.latency_ms}ms`);
    loadHistory();
  };

  return (
    <Page
      title="检测测试台"
      subtitle="上传图片或取监控当前帧，用已加载模型试跑；用于验证新导入模型"
    >
      <div className="grid row2">
        <div className="card">
          <div className="card-title">输入</div>
          <div className="file-upload">
            <input
              ref={fileRef}
              type="file"
              accept=".jpg,.jpeg,.png,.webp"
              hidden
              onChange={(e) => setImgName(e.target.files?.[0]?.name || "")}
            />
            <button
              type="button"
              className="ghost file-upload__pick"
              disabled={busy.run}
              aria-describedby="detect-file-status detect-file-hint"
              onClick={() => fileRef.current?.click()}
            >
              <Icon name="upload" size={16} />
              {imgName ? "更换图片" : "选择图片"}
            </button>
            <div className="file-upload__info">
              <span id="detect-file-status" className="file-upload__name" title={imgName || undefined} role="status">
                {imgName || "尚未选择图片"}
              </span>
              <span id="detect-file-hint" className="file-upload__hint">支持 JPG、PNG、WebP</span>
            </div>
            <button
              className="file-upload__run"
              disabled={busy.run || !imgName}
              onClick={runTest}
            >
              {busy.run ? "检测中…" : "开始检测"}
            </button>
          </div>
          <div className="toolbar" style={{ marginTop: 12 }}>
            <Select ref={camRef}>
              {cams.length ? (
                cams.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))
              ) : (
                <option value="">无可用监控</option>
              )}
            </Select>
            <button className="ghost" disabled={busy.cam} onClick={runCamFrame}>
              用监控当前帧
            </button>
          </div>
          <div className="toolbar" style={{ marginTop: 14 }}>
            <label style={{ margin: 0, color: "var(--muted)" }}>conf 覆盖</label>
            <input
              style={{ width: 90 }}
              type="number"
              step={0.05}
              min={0}
              max={1}
              placeholder="默认"
              value={conf}
              onChange={(e) => setConf(e.target.value)}
            />
            <div className="inline-checks" style={{ padding: 0 }}>
              {loadedModels.length ? (
                loadedModels.map((m) => (
                  <label key={m}>
                    <input
                      type="checkbox"
                      checked={picked.includes(m)}
                      onChange={() =>
                        setPicked((p) =>
                          p.includes(m) ? p.filter((x) => x !== m) : [...p, m],
                        )
                      }
                    />{" "}
                    {m}
                  </label>
                ))
              ) : (
                <span className="muted">没有已加载的模型</span>
              )}
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            {result?.annotated_url ? (
              <img
                className="thumb"
                title="点击放大"
                style={{ cursor: "zoom-in" }}
                src={`${result.annotated_url}?t=${imgTick}`}
                onClick={() => showImage(`${result.annotated_url}?t=${imgTick}`, "检测结果")}
                alt="检测结果"
              />
            ) : null}
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <div className="card-title">检测结果</div>
            <pre className="log" style={{ maxHeight: 280 }}>
              {json}
            </pre>
          </div>
          <div className="card">
            <div className="card-title">最近记录</div>
            <div className="feed">
              {history === null ? null : history.length ? (
                history.map((r, i) => (
                  <div className="item" key={i}>
                    <span className="t">{r.time}</span>
                    <span>{r.detections.length} 目标</span>
                    <span className="muted mono">{r.latency_ms}ms</span>
                    <a
                      href={r.annotated_url}
                      onClick={(e) => {
                        e.preventDefault();
                        showImage(r.annotated_url, "检测结果");
                      }}
                    >
                      查看
                    </a>
                  </div>
                ))
              ) : (
                <Empty>暂无记录</Empty>
              )}
            </div>
          </div>
        </div>
      </div>
    </Page>
  );
}
