import { Select } from "../ui/Select";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { Page } from "../layout/Page";

export default function LogsPage() {
  const [level, setLevel] = useState("");
  const [tail, setTail] = useState("500");
  const [auto, setAuto] = useState(true);
  const [lines, setLines] = useState<string[] | null>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const autoRef = useRef(auto);
  autoRef.current = auto;

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
            style={{ minWidth: 130 }}
            value={level}
            onChange={(e) => setLevel(e.target.value)}
          >
            <option value="">全部级别</option>
            <option>DEBUG</option>
            <option>INFO</option>
            <option>WARNING</option>
            <option>ERROR</option>
            <option>CRITICAL</option>
          </Select>
          <Select
            style={{ minWidth: 150 }}
            value={tail}
            onChange={(e) => setTail(e.target.value)}
          >
            <option value="500">最近 500 行</option>
            <option value="2000">最近 2000 行</option>
            <option value="5000">最近 5000 行</option>
          </Select>
          <button className="mini" onClick={refresh}>
            刷新
          </button>
        </div>
        <pre className="log" ref={preRef}>
          {lines === null ? (
            <span className="muted-lb">加载中…</span>
          ) : lines.length ? (
            lines.map((l, i) => (
              <div className={cls(l)} key={i}>
                {l}
              </div>
            ))
          ) : (
            <span className="muted-lb">暂无日志</span>
          )}
        </pre>
      </div>
    </Page>
  );
}
