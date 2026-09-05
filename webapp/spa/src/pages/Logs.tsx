import { Select } from "../ui/Select";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { useConfirm } from "../ui/Confirm";
import { useBusy } from "../ui/badges";
import { useToast } from "../ui/Toast";

export default function LogsPage() {
  const [level, setLevel] = useState("");
  const [tail, setTail] = useState("500");
  const [auto, setAuto] = useState(true);
  const [lines, setLines] = useState<string[] | null>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const autoRef = useRef(auto);
  autoRef.current = auto;
  const confirm = useConfirm();
  const toast = useToast();
  const { busy, wrap } = useBusy();

  const refresh = useCallback(async () => {
    const data = await api<{ lines: string[] }>(
      `/api/logs?tail=${tail}` + (level ? `&level=${level}` : ""),
    );
    const el = preRef.current;
    const atBottom = el
      ? el.scrollTop + el.clientHeight >= el.scrollHeight - 30
      : true;
    setLines(data.lines);
    // 等 DOM 更新后再恢复滚动位置
    requestAnimationFrame(() => {
      const p = preRef.current;
      if (p && (atBottom || autoRef.current)) p.scrollTop = p.scrollHeight;
    });
  }, [level, tail]);

  useEffect(() => {
    refresh();
    const timer = setInterval(() => {
      if (autoRef.current) refresh();
    }, 2000);
    return () => clearInterval(timer);
  }, [refresh]);

  const clearLogs = wrap("clear", async () => {
    if (!(await confirm(
      "确定清空当前日志文件？该操作不可恢复，历史轮转备份会保留。",
      { danger: true, okText: "清空日志" },
    ))) return;
    try {
      await api("/api/logs/clear", { method: "POST", body: {} });
      toast("日志已清空");
      await refresh();
    } catch (e) {
      toast((e as Error).message || "清空失败，请重试", false);
    }
  });

  const cls = (l: string) =>
    l.includes("[ERROR]") || l.includes("[CRITICAL]")
      ? "ERROR"
      : l.includes("[WARNING]")
        ? "WARN"
        : l.includes("[INFO]")
          ? "INFO"
          : "";

  return (
    <Page
      title="日志"
      subtitle="实时滚动查看检测系统日志"
      actions={
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            color: "var(--text)",
            margin: 0,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={auto}
            onChange={(e) => setAuto(e.target.checked)}
          />{" "}
          自动刷新
        </label>
      }
    >
      <div className="card">
        <div className="toolbar" style={{ marginBottom: 14 }}>
          <Select
            aria-label="日志等级"
            style={{ minWidth: 130 }}
            value={level}
            disabled={busy.clear}
            onChange={(e) => setLevel(e.target.value)}
          >
            <option value="">全部级别</option>
            <option value="DEBUG">DEBUG</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
            <option value="CRITICAL">CRITICAL</option>
          </Select>
          <Select
            aria-label="日志行数"
            style={{ minWidth: 150 }}
            value={tail}
            disabled={busy.clear}
            onChange={(e) => setTail(e.target.value)}
          >
            <option value="500">最近 500 行</option>
            <option value="2000">最近 2000 行</option>
            <option value="5000">最近 5000 行</option>
          </Select>
          <button className="ghost mini" disabled={busy.clear} onClick={clearLogs}>
            <Icon name="trash" size={14} />{busy.clear ? "正在清空…" : "清空日志"}
          </button>
          <button className="mini" disabled={busy.clear} onClick={refresh}>
            刷新
          </button>
          <span className="muted" style={{ marginLeft: "auto" }}>
            {lines?.length ? `当前显示 ${lines.length} 行` : ""}
          </span>
        </div>
        <pre className="log log-empty-state" ref={preRef} aria-live="polite">
          {lines === null ? (
            <span className="muted-lb">加载中…</span>
          ) : lines.length ? (
            lines.map((l, i) => (
              <div className={cls(l)} key={i}>
                {l}
              </div>
            ))
          ) : (
            <span className="muted-lb">
              {level ? `当前筛选下暂无 ${level} 日志，可切换为「全部级别」` : "暂无日志"}
            </span>
          )}
        </pre>
      </div>
    </Page>
  );
}
