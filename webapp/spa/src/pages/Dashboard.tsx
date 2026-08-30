import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { AlertItem, StorageUsage, TrendDay } from "../api/types";
import { Page } from "../layout/Page";
import { usePolling } from "../hooks/usePolling";
import { BarChart } from "../ui/BarChart";
import { Chip, Empty, StatusBadge } from "../ui/badges";

function fmtUptime(s: number | null | undefined) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function tsToTime(ts?: number) {
  return ts
    ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false })
    : "-";
}

interface CamLite {
  id: string;
  name: string;
  enabled: boolean;
  connected: boolean;
  thread_alive: boolean;
}

export default function DashboardPage() {
  const [cams, setCams] = useState({ v: "—", sub: "", ico: "" });
  const [alerts, setAlerts] = useState({ v: "—", sub: "", pend: 0, ico: "" });
  const [snaps, setSnaps] = useState({ v: "—", sub: "", ico: "green" });
  const [up, setUp] = useState({ v: "—", sub: "" });
  const [trend, setTrend] = useState<TrendDay[]>([]);
  const [feed, setFeed] = useState<AlertItem[] | null>(null);
  const [banner, setBanner] = useState("");

  const refresh = useCallback(async () => {
    const [stats, camList, usage, tr, pending] = await Promise.all([
      api<{ standalone?: boolean; uptime?: number; frames_processed?: number; avg_fps?: number | string }>(
        "/api/system/stats",
      ),
      api<{ cameras: CamLite[] }>("/api/cameras"),
      api<StorageUsage>("/api/storage/usage"),
      api<{ trend: TrendDay[] }>("/api/system/stats/history?days=7"),
      api<{ total?: number }>("/api/alerts?status=new&limit=1"),
    ]);

    const camsAll = camList.cameras;
    const enabled = camsAll.filter((c) => c.enabled);
    const online = enabled.filter((c) => c.connected);
    const offline = enabled.filter((c) => !c.connected && !c.thread_alive);
    const recon = enabled.filter((c) => !c.connected && c.thread_alive);
    setCams({
      v: `${online.length} / ${enabled.length}`,
      sub:
        `共 ${camsAll.length} 路` +
        (offline.length
          ? ` · <span class="bad">离线 ${offline.length}</span>`
          : "") +
        (recon.length
          ? ` · <span class="warn">重连 ${recon.length}</span>`
          : ""),
      ico: offline.length ? "red" : recon.length ? "yellow" : "",
    });

    const week = tr.trend.reduce((s, d) => s + d.total, 0);
    const pend = pending.total ?? 0;
    setAlerts({
      v: pend.toLocaleString("zh-CN"),
      sub: `今日 ${tr.trend.at(-1)?.total ?? 0} 条 · 近 7 天 ${week.toLocaleString("zh-CN")} 条`,
      pend,
      ico: pend > 0 ? "red" : "green",
    });

    setSnaps({
      v: usage.snapshots_total_mb + " MB",
      sub: `磁盘已用 ${usage.disk_used_pct}% · 剩 ${usage.disk_free_gb}GB`,
      ico:
        usage.watermark === "red"
          ? "red"
          : usage.watermark === "yellow"
            ? "yellow"
            : "green",
    });

    setUp({
      v: stats.standalone ? "未运行" : fmtUptime(stats.uptime),
      sub: stats.standalone
        ? "独立只读模式"
        : `累计 ${stats.frames_processed} 帧 · ${stats.avg_fps} fps`,
    });

    setBanner(
      usage.watermark !== "ok"
        ? `⚠ 磁盘空间${usage.watermark === "red" ? "严重不足" : "偏低"}（已用 ${usage.disk_used_pct}%），建议清理历史快照或缩短保留天数。`
        : "",
    );
    setTrend(tr.trend);

    const list = await api<{ items: AlertItem[] }>("/api/alerts?limit=10");
    setFeed(list.items);
  }, []);

  usePolling(refresh, 3000);

  const camIcon = (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
      <path d="M23 7l-7 5 7 5V7z" />
      <rect x="1" y="5" width="15" height="14" rx="2" />
    </svg>
  );
  const alertIcon = (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
  const snapIcon = (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <circle cx="8.5" cy="8.5" r="1.5" />
      <polyline points="21 15 16 10 5 21" />
    </svg>
  );

  return (
    <Page title="总览" subtitle="系统运行状态与实时告警总览">
      {banner ? <div className="banner">{banner}</div> : null}
      <div className="grid cards">
        <div
          className="stat link"
          onClick={() => (window.location.href = "/cameras")}
          title="进入监控管理"
        >
          <div className={`ico ${cams.ico}`}>{camIcon}</div>
          <div>
            <div className="k">监控状态</div>
            <div className="v">{cams.v}</div>
            <div className="sub" dangerouslySetInnerHTML={{ __html: cams.sub }} />
          </div>
        </div>
        <div
          className="stat link"
          onClick={() => (window.location.href = "/alerts")}
          title="查看告警记录"
        >
          <div className={`ico ${alerts.ico}`}>{alertIcon}</div>
          <div>
            <div className="k">待处理告警</div>
            <div className={`v${alerts.pend > 0 ? " alerting" : ""}`}>{alerts.v}</div>
            <div className="sub">{alerts.sub}</div>
          </div>
        </div>
        <div
          className="stat link"
          onClick={() => (window.location.href = "/snapshots")}
          title="查看快照库"
        >
          <div className={`ico ${snaps.ico}`}>{snapIcon}</div>
          <div>
            <div className="k">快照占用</div>
            <div className="v">{snaps.v}</div>
            <div className="sub">{snaps.sub}</div>
          </div>
        </div>
        <div className="stat">
          <div className="ico">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
          </div>
          <div>
            <div className="k">运行时长</div>
            <div className="v">{up.v}</div>
            <div className="sub">{up.sub}</div>
          </div>
        </div>
      </div>

      <div className="grid row2" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="card-title">
            <span>
              实时告警 <span className="muted">每 3 秒刷新</span>
            </span>
            <span className="cam-head">
              {alerts.pend > 0 ? (
                <Chip text={`${alerts.pend} 条待处理`} color="red" />
              ) : null}
              <a className="more" href="/alerts">
                查看全部 →
              </a>
            </span>
          </div>
          <div className="feed" id="alert-feed">
            {feed === null ? (
              <Empty>加载中…</Empty>
            ) : feed.length ? (
              feed.map((a) => (
                <div
                  key={a.id}
                  className={
                    "item " +
                    (a.status === "new"
                      ? "hot"
                      : a.status === "resolved" || a.status === "false_positive"
                        ? "dim"
                        : "")
                  }
                  title={tsToTime(a.timestamp)}
                >
                  <span className="t">{tsToTime(a.timestamp).slice(11)}</span>
                  <b>{a.camera_id}</b>
                  <Chip text={"R" + String(a.rule_id).padStart(2, "0")} color="blue" />
                  <span>{a.rule_name}</span>
                  <span className="muted mono">conf {a.confidence?.toFixed(2)}</span>
                  <StatusBadge status={a.status} />
                </div>
              ))
            ) : (
              <Empty>暂无告警记录</Empty>
            )}
          </div>
        </div>
        <div className="card chart">
          <div className="card-title">
            <span>近 7 天告警趋势</span>
            <span className="legend">
              <span className="li">
                <span className="sw" style={{ background: "var(--red)" }} />
                确认违规
              </span>
              <span className="li">
                <span
                  className="sw"
                  style={{ background: "linear-gradient(180deg,#22d3ee,#4d9fff)" }}
                />
                未处理
              </span>
              <span className="li">
                <span className="sw" style={{ background: "rgba(122,138,160,.45)" }} />
                误报
              </span>
            </span>
          </div>
          <BarChart
            height="fill"
            data={trend.map((d) => ({
              label: d.day,
              value: d.total,
              segments: [
                { v: d.confirmed, c: "var(--red)", name: "确认违规" },
                { v: d.pending, c: "url(#grad)", name: "未处理" },
                { v: d.false_positive, c: "rgba(122,138,160,.45)", name: "误报" },
              ],
            }))}
          />
        </div>
      </div>
    </Page>
  );
}
