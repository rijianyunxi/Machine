import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AlertItem, StorageUsage, TrendDay } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { usePolling } from "../hooks/usePolling";
import { useLightbox } from "../ui/Lightbox";
import { BarChart } from "../ui/BarChart";
import { Chip, StatusBadge } from "../ui/badges";

function fmtUptime(s: number | null | undefined) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function toLocalDate(ts?: number) {
  if (ts == null || !Number.isFinite(ts)) return null;
  const date = new Date(ts * 1000);
  return Number.isNaN(date.getTime()) ? null : date;
}

function pad2(value: number) {
  return String(value).padStart(2, "0");
}

function tsToTime(ts?: number) {
  const date = toLocalDate(ts);
  if (!date) return "-";
  return [
    date.getFullYear(),
    pad2(date.getMonth() + 1),
    pad2(date.getDate()),
  ].join("-") +
    ` ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function tsToClock(ts?: number) {
  const date = toLocalDate(ts);
  return date
    ? `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
    : "-";
}

interface CamLite {
  id: string;
  name: string;
  enabled: boolean;
  connected: boolean;
  thread_alive: boolean;
}

function ViolationSnapshot({ alert, onOpen }: { alert: AlertItem; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const title = alert.rule_name || `规则 ${alert.rule_id}`;
  const source = alert.snapshot_url || "";
  const thumb = source.startsWith("/snapshots/")
    ? `/api/snapshots/thumb?${new URLSearchParams({ p: decodeURIComponent(source.slice("/snapshots/".length)), w: "420" })}`
    : source;
  return (
    <figure className="dashboard-snapshot">
      <button className="dashboard-snapshot__image" onClick={onOpen} disabled={failed}
        aria-label={`查看违规快照：${title}，${alert.camera_name || alert.camera_id}`}>
        {failed ? <span className="dashboard-snapshot__missing"><Icon name="images" size={24} />图片已失效</span> : (
          <img src={thumb} alt={`${alert.camera_name || alert.camera_id} · ${title}`} onError={() => setFailed(true)} />
        )}
        <span className="dashboard-snapshot__label" title={title}>{title}</span>
        {alert.status === "new" && <span className="dashboard-snapshot__pending">待复核</span>}
      </button>
      <figcaption>
        <span title={alert.camera_name || alert.camera_id}>{alert.camera_name || alert.camera_id}</span>
        <time dateTime={toLocalDate(alert.timestamp)?.toISOString()}>{tsToTime(alert.timestamp)}</time>
      </figcaption>
    </figure>
  );
}

export default function DashboardPage() {
  const [cams, setCams] = useState<{ v: string; sub: React.ReactNode; ico: string }>({
    v: "—",
    sub: "",
    ico: "",
  });
  const [alerts, setAlerts] = useState({ v: "—", sub: "", pend: 0, ico: "" });
  const [snaps, setSnaps] = useState({ v: "—", sub: "", ico: "green" });
  const [up, setUp] = useState({ v: "—", sub: "" });
  const [trend, setTrend] = useState<TrendDay[]>([]);
  const { showGallery } = useLightbox();
  const [recentSnapshots, setRecentSnapshots] = useState<AlertItem[] | null>(null);
  const [snapshotsError, setSnapshotsError] = useState(false);
  const [feed, setFeed] = useState<AlertItem[] | null>(null);
  const [feedError, setFeedError] = useState(false);
  const [feedUpdated, setFeedUpdated] = useState<number | null>(null);
  const [banner, setBanner] = useState<{ level: string; msg: string } | null>(null);

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
      sub: (
        <>
          共 {camsAll.length} 路
          {offline.length ? (
            <>
              {" · "}
              <span className="bad">离线 {offline.length}</span>
            </>
          ) : null}
          {recon.length ? (
            <>
              {" · "}
              <span className="warn">重连 {recon.length}</span>
            </>
          ) : null}
        </>
      ),
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
        ? {
            level: usage.watermark,
            msg: `磁盘空间${usage.watermark === "red" ? "严重不足" : "偏低"}（已用 ${usage.disk_used_pct}%），建议清理历史快照或缩短保留天数。`,
          }
        : null,
    );
    setTrend(tr.trend);

  }, []);

  const refreshFeed = useCallback(async () => {
    try {
      const list = await api<{ items: AlertItem[] }>("/api/alerts?limit=10");
      setFeed(list.items);
      setFeedError(false);
      setFeedUpdated(Date.now() / 1000);
    } catch {
      setFeedError(true);
    }
  }, []);
  const refreshSnapshots = useCallback(async () => {
    try {
      const result = await api<{ items: AlertItem[] }>("/api/alerts/recent-snapshots");
      setRecentSnapshots(result.items.slice(0, 3));
      setSnapshotsError(false);
    } catch {
      setSnapshotsError(true);
    }
  }, []);
  usePolling(refreshSnapshots, 3000);
  usePolling(refresh, 3000);
  usePolling(refreshFeed, 3000);

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
      {banner ? (
        <div className={"banner" + (banner.level === "red" ? " red" : "")}>
          <Icon name="alert-triangle" size={15} />
          <span>{banner.msg}</span>
        </div>
      ) : null}
      <div className="grid cards">
        <Link to="/cameras" className="stat link" title="进入监控管理">
          <div className={`ico ${cams.ico}`}>{camIcon}</div>
          <div>
            <div className="k">监控状态</div>
            <div className="v">{cams.v}</div>
            <div className="sub">{cams.sub}</div>
          </div>
        </Link>
        <Link to="/alerts" className="stat link" title="查看告警记录">
          <div className={`ico ${alerts.ico}`}>{alertIcon}</div>
          <div>
            <div className="k">待处理告警</div>
            <div className={`v${alerts.pend > 0 ? " alerting" : ""}`}>{alerts.v}</div>
            <div className="sub">{alerts.sub}</div>
          </div>
        </Link>
        <Link to="/snapshots" className="stat link" title="查看快照库">
          <div className={`ico ${snaps.ico}`}>{snapIcon}</div>
          <div>
            <div className="k">快照占用</div>
            <div className="v">{snaps.v}</div>
            <div className="sub">{snaps.sub}</div>
          </div>
        </Link>
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

      <div className="dashboard-workspace">
        <div className="card dashboard-live-card">
          <div className="card-title">
            <span>
              实时告警
            </span>
            <span className="cam-head">
              {alerts.pend > 0 ? (
                <Chip text={`${alerts.pend} 条待处理`} color="red" />
              ) : null}
              <Link className="more" to="/alerts" style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
                查看全部 <Icon name="arrow-right" size={12} />
              </Link>
            </span>
          </div>
          <div className="dashboard-feed-meta">
            <span>最近 10 条 · 每 3 秒自动刷新</span>
            <span>{feedError ? "更新暂停" : feedUpdated ? `更新于 ${tsToClock(feedUpdated)}` : "正在连接"}</span>
          </div>
          {feedError && <div className="banner red" role="status">告警暂时无法更新{feed?.length ? "，下方保留上次记录" : ""}<button className="ghost" onClick={refreshFeed}>重试</button></div>}
          <div className="dashboard-alert-feed" id="alert-feed">
            {feed?.length ? (
              <ul className="dashboard-alert-list" aria-label="最近告警">
                {feed.map((a) => (
                  <li key={a.id}>
                    <Link to="/alerts" className={`dashboard-alert-row${a.status === "new" ? " is-new" : ""}`}>
                      <span className="dashboard-alert-icon" aria-hidden="true"><Icon name="alert-triangle" size={18} /></span>
                      <div className="dashboard-alert-content">
                        <div className="dashboard-alert-heading"><strong>{a.rule_name || `规则 ${a.rule_id}`}</strong><StatusBadge status={a.status} /></div>
                        <div className="dashboard-alert-detail"><span>{a.camera_name || a.camera_id}</span><span>R{String(a.rule_id).padStart(2, "0")}</span><span>置信度 {a.confidence == null ? "—" : `${Math.round(a.confidence * 100)}%`}</span></div>
                        <time className="dashboard-alert-time" dateTime={toLocalDate(a.timestamp)?.toISOString()}>{tsToTime(a.timestamp)}</time>
                      </div>
                      <Icon name="chevron-right" size={16} />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="dashboard-feed-empty" role="status">
                <span className="dashboard-feed-empty__icon" aria-hidden="true"><Icon name={feedError ? "alert-triangle" : "alerts"} size={28} /></span>
                <h3>{feedError ? "暂时无法获取告警" : feed === null ? "正在获取最新告警" : "暂无告警记录"}</h3>
                <p>{feedError ? "请检查服务连接，系统会自动重试。" : feed === null ? "加载完成后将在这里展示最近记录。" : "新的告警将在这里自动出现，无需手动刷新。暂无记录不代表监控已正常运行。"}</p>
                {!feedError && feed !== null && <Link className="dashboard-feed-link" to="/cameras">检查监控状态 <Icon name="arrow-right" size={14} /></Link>}
              </div>
            )}
          </div>
        </div>
        <div className="dashboard-right-stack">
          <section className="card dashboard-snapshots-card" aria-labelledby="recent-snapshots-title">
            <div className="card-title">
              <span id="recent-snapshots-title">最新违规快照</span>
              <Link className="more" to="/snapshots">查看快照库 <Icon name="arrow-right" size={12} /></Link>
            </div>
            <p className="dashboard-snapshots-note">仅展示最新 3 张 · 已排除误报与已清理快照</p>
            {snapshotsError && <div className="dashboard-snapshots-error" role="status">快照更新失败{recentSnapshots?.length ? "，暂显示上次结果" : ""}<button className="mini ghost" onClick={refreshSnapshots}>重试快照</button></div>}
            {recentSnapshots?.length ? (
              <div className="dashboard-snapshot-grid">
                {recentSnapshots.map((alert, index) => (
                  <ViolationSnapshot key={`${alert.id}:${alert.snapshot_url}`} alert={alert} onOpen={() => showGallery(
                    recentSnapshots.map((a) => ({ src: a.snapshot_url!, title: `${a.rule_name} · ${a.camera_name || a.camera_id} · ${tsToTime(a.timestamp)}` })), index,
                  )} />
                ))}
              </div>
            ) : (
              <div className="dashboard-snapshots-empty" role="status">
                <Icon name="images" size={28} />
                <strong>{snapshotsError ? "暂时无法获取快照" : recentSnapshots === null ? "正在加载违规快照" : "暂无违规快照"}</strong>
                <span>{snapshotsError ? "请重试或稍候等待自动更新。" : "告警产生图片后，将在这里展示最近的违规现场。"}</span>
              </div>
            )}
          </section>
          <div className="card chart dashboard-trend-card">
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
                    style={{ background: "linear-gradient(180deg, var(--accent-2), var(--accent))" }}
                  />
                  未处理
                </span>
                <span className="li">
                  <span className="sw" style={{ background: "var(--muted)" }} />
                  误报
                </span>
              </span>
            </div>
            <BarChart
              height={210}
              data={trend.map((d) => ({
                label: d.day,
                value: d.total,
                segments: [
                  { v: d.confirmed, c: "var(--red)", name: "确认违规" },
                  { v: d.pending, c: "var(--accent)", name: "未处理" },
                  { v: d.false_positive, c: "var(--muted)", name: "误报" },
                ],
              }))}
            />
          </div>
        </div>
      </div>
    </Page>
  );
}
