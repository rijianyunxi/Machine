import { Select } from "../ui/Select";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AlertItem, AlertStatus, Camera, RuleEntry } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { useConfirm } from "../ui/Confirm";
import { Modal } from "../ui/Modal";
import { useToast } from "../ui/Toast";
import { Chip, Empty, StatusBadge, useBusy } from "../ui/badges";
import { useLightbox } from "../ui/Lightbox";

function tsToTime(ts: number) {
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}

function relTime(ts: number) {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  return Math.floor(diff / 86400) + " 天前";
}

function pathToSnapshotUrl(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const marker = "storage/snapshots/";
  const markerIndex = normalized.toLowerCase().lastIndexOf(marker);
  const relative =
    markerIndex >= 0
      ? normalized.slice(markerIndex + marker.length)
      : normalized.replace(/^\/+/, "");

  return "/snapshots/" + relative.split("/").filter(Boolean).map(encodeURIComponent).join("/");
}

function snapshotUrl(alert: AlertItem): string | null {
  return alert.snapshot_url || (alert.snapshot_path ? pathToSnapshotUrl(alert.snapshot_path) : null);
}

const LIMIT = 50;

export default function AlertsPage() {
  const [cams, setCams] = useState<Camera[]>([]);
  const [rules, setRules] = useState<RuleEntry[]>([]);
  const [fCam, setFCam] = useState("");
  const [fRule, setFRule] = useState("");
  const [fStatus, setFStatus] = useState("");
  const [fDays, setFDays] = useState("7");
  const [items, setItems] = useState<AlertItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [target, setTarget] = useState<{ id: number; status: AlertStatus } | null>(null);
  const [note, setNote] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const requestSeq = useRef(0);
  const allRef = useRef<HTMLInputElement>(null);
  const confirm = useConfirm();
  const toast = useToast();
  const { showImage } = useLightbox();
  const { busy, wrap } = useBusy();

  const refresh = useCallback(
    async (off = offset) => {
      const seq = ++requestSeq.current;
      setLoading(true);
      setLoadError("");
      setSelected([]);
      try {
        const p = new URLSearchParams({ limit: String(LIMIT), offset: String(off) });
        if (fCam) p.set("camera", fCam);
        if (fRule) p.set("rule", fRule);
        if (fStatus) p.set("status", fStatus);
        if (fDays) p.set("days", fDays);
        let data = await api<{ items: AlertItem[]; total: number }>("/api/alerts?" + p);
        if (off > 0 && !data.items.length) {
          off = Math.max(0, Math.floor((data.total - 1) / LIMIT) * LIMIT);
          p.set("offset", String(off));
          data = await api<{ items: AlertItem[]; total: number }>("/api/alerts?" + p);
        }
        if (seq !== requestSeq.current) return;
        setItems(data.items);
        setTotal(data.total);
        setOffset(off);
      } catch (e) {
        if (seq === requestSeq.current) setLoadError((e as Error).message || "告警加载失败");
      } finally {
        if (seq === requestSeq.current) setLoading(false);
      }
    },
    [fCam, fRule, fStatus, fDays, offset],
  );

  useEffect(() => {
    Promise.all([
      api<{ cameras: Camera[] }>("/api/cameras"),
      api<{ rules: RuleEntry[] }>("/api/rules"),
    ]).then(([c, r]) => {
      setCams(c.cameras);
      setRules(r.rules);
    }).catch((e) => toast((e as Error).message, false));
    refresh(0); // 初始查询（此时为默认筛选：最近 7 天）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const query = () => refresh(0);
  const page = (delta: number) => {
    const next = offset + delta * LIMIT;
    if (next < 0 || next >= total) return;
    refresh(next);
  };

  useEffect(() => {
    setNote("");
  }, [target?.id]);

  const submitStatus = wrap("mark", async () => {
    if (!target) return;
    try {
      await api(`/api/alerts/${target.id}/status`, {
        method: "POST",
        body: { status: target.status, note: note.trim() || null },
      });
      toast("已更新");
      setTarget(null);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const locked = loading || !!busy.remove;
  const allSelected = !!items?.length && selected.length === items.length;
  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = selected.length > 0 && !allSelected;
  }, [selected, allSelected]);

  const deleteSelected = wrap("remove", async () => {
    const ids = [...selected];
    if (!ids.length || loading) return;
    if (!(await confirm(
      `确定删除选中的 ${ids.length} 条告警？对应快照及缩略图将从快照库同步删除，且无法恢复。仍被其他告警引用的快照会保留。`,
      { danger: true, okText: "删除告警及快照" },
    ))) return;
    try {
      const result = await api<{ deleted: number; snapshots_deleted: number; shared_snapshots_kept: number; cleanup_pending: boolean }>(
        "/api/alerts/batch-delete", { method: "POST", body: { ids } },
      );
      toast(`已删除 ${result.deleted} 条告警、${result.snapshots_deleted} 张快照` +
        (result.shared_snapshots_kept ? `；保留 ${result.shared_snapshots_kept} 张共享快照` : ""));
      if (result.cleanup_pending) toast("记录已删除，隔离文件尚未清理完成，请联系管理员检查服务日志", false);
      await refresh();
    } catch (e) {
      toast((e as Error).message || "删除失败，请重试", false);
    }
  });

  return (
    <Page
      title="告警记录"
      subtitle="复核告警：确认真实违规 / 标记误报，误报率用于反哺调参"
    >
      <div className="card">
        <fieldset className="filter-bar alerts-filters" disabled={locked}>
          <Select
            style={{ minWidth: 150 }}
            disabled={locked}
            aria-label="筛选监控"
            value={fCam}
            onChange={(e) => setFCam(e.target.value)}
          >
            <option value="">全部监控</option>
            {cams.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <Select
            style={{ minWidth: 170 }}
            disabled={locked}
            aria-label="筛选规则"
            value={fRule}
            onChange={(e) => setFRule(e.target.value)}
          >
            <option value="">全部规则</option>
            {rules.map((r) => (
              <option key={r.id} value={r.id}>
                R{String(r.id).padStart(2, "0")} {r.name}
              </option>
            ))}
          </Select>
          <Select
            style={{ minWidth: 130 }}
            disabled={locked}
            aria-label="筛选状态"
            value={fStatus}
            onChange={(e) => setFStatus(e.target.value)}
          >
            <option value="">全部状态</option>
            <option value="new">新告警</option>
            <option value="confirmed">确认违规</option>
            <option value="false_positive">误报</option>
            <option value="resolved">已处理</option>
          </Select>
          <Select
            style={{ minWidth: 130 }}
            disabled={locked}
            aria-label="筛选时间"
            value={fDays}
            onChange={(e) => setFDays(e.target.value)}
          >
            <option value="1">最近 24 小时</option>
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="">全部时间</option>
          </Select>
          {selected.length > 0 && (
            <button className="ghost mini" disabled={locked} onClick={() => setSelected([])}>
              取消选择
            </button>
          )}
          <button className="danger mini" disabled={locked || !selected.length || !!loadError} onClick={deleteSelected}>
            <Icon name="trash" size={14} />{busy.remove ? "正在处理…" : `批量删除${selected.length ? ` (${selected.length})` : ""}`}
          </button>
          <button className="mini" onClick={query}>
            查询
          </button>
          <span className="muted" style={{ marginLeft: "auto" }}>
            {items ? `共 ${total} 条` : ""}
          </span>
        </fieldset>
        {loadError && <div className="banner red" role="alert">加载失败：{loadError}<button className="ghost" disabled={locked} onClick={() => refresh()}>重试</button></div>}
        <div className="table-wrap" aria-busy={loading}>
          <table>
            <thead>
              <tr>
                <th className="alerts-select-cell">
                  <input ref={allRef} type="checkbox" aria-label="全选当前页告警"
                    checked={allSelected} disabled={locked || !items?.length || !!loadError}
                    onChange={(e) => setSelected(e.target.checked ? (items || []).map((a) => a.id) : [])} />
                </th>
                <th>时间</th>
                <th>监控</th>
                <th>规则</th>
                <th>置信度</th>
                <th>快照</th>
                <th>状态</th>
                <th style={{ textAlign: "right" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {items === null ? (
                <tr>
                  <td colSpan={8}>
                    <Empty>加载中…</Empty>
                  </td>
                </tr>
              ) : items.length ? (
                items.map((a) => (
                  <tr key={a.id} className={selected.includes(a.id) ? "alerts-row-selected" : undefined}>
                    <td className="alerts-select-cell">
                      <input type="checkbox" aria-label={`选择告警 ${a.id}`} checked={selected.includes(a.id)}
                        disabled={locked || !!loadError}
                        onChange={(e) => setSelected((ids) => e.target.checked ? [...ids, a.id] : ids.filter((id) => id !== a.id))} />
                    </td>
                    <td>
                      <div className="mono">{tsToTime(a.timestamp)}</div>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {relTime(a.timestamp)}
                      </div>
                    </td>
                    <td>{a.camera_name || a.camera_id}</td>
                    <td>
                      <Chip text={"R" + String(a.rule_id).padStart(2, "0")} color="blue" />{" "}
                      {a.rule_name}
                    </td>
                    <td className="mono">{a.confidence?.toFixed(2)}</td>
                    <td>
                      {a.snapshot_status === "available" && snapshotUrl(a) ? (
                        <a
                          href={snapshotUrl(a)!}
                          onClick={(e) => {
                            e.preventDefault();
                            showImage(
                              snapshotUrl(a)!,
                              `${a.camera_name || a.camera_id} · ${a.rule_name}`,
                            );
                          }}
                        >
                          查看
                        </a>
                      ) : (
                        <span className="muted">
                          {{
                            none: "无截图",
                            available: "查看",
                            cleaned: "已清理",
                            missing: "文件缺失",
                          }[a.snapshot_status || (a.snapshot_path ? "missing" : "none")] || "文件缺失"}
                        </span>
                      )}
                    </td>
                    <td>
                      <StatusBadge status={a.status} />
                    </td>
                    <td className="actions" style={{ textAlign: "right" }}>
                      {a.status === "new" ? (
                        <>
                          <button
                            className="mini danger"
                            disabled={locked}
                            onClick={() => setTarget({ id: a.id, status: "confirmed" })}
                          >
                            确认违规
                          </button>
                          <button
                            className="mini ghost"
                            disabled={locked}
                            onClick={() => setTarget({ id: a.id, status: "false_positive" })}
                          >
                            误报
                          </button>
                        </>
                      ) : null}
                      {a.status !== "resolved" ? (
                        <button
                          className="mini ghost"
                          disabled={locked}
                            onClick={() => setTarget({ id: a.id, status: "resolved" })}
                        >
                          完结
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={8}>
                    <Empty>无符合条件的告警</Empty>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="pager">
          <button className="mini ghost" disabled={locked || offset <= 0} onClick={() => page(-1)}>
            上一页
          </button>
          <span className="muted">
            {total
              ? `${Math.min(offset + 1, total)}–${Math.min(offset + LIMIT, total)} / ${total} · 第 ${Math.floor(offset / LIMIT) + 1} 页`
              : ""}
          </span>
          <button
            className="mini ghost"
            disabled={locked || offset + LIMIT >= total}
            onClick={() => page(1)}
          >
            下一页
          </button>
        </div>
      </div>

      {target && (
        <Modal
          title={
            { confirmed: "确认违规", false_positive: "标记为误报", resolved: "完结处理", new: "标记告警" }[
              target.status
            ]
          }
          width={440}
          onClose={() => setTarget(null)}
          footer={
            <>
              <button className="ghost" disabled={locked}
                            onClick={() => setTarget(null)}>
                取消
              </button>
              <button disabled={busy.mark} onClick={submitStatus}>
                确定
              </button>
            </>
          }
        >
          <label>备注（可选，误报原因 / 处理说明）</label>
          <textarea
            style={{ width: "100%" }}
            rows={3}
            placeholder="例如：画面反光被误识别为烟头"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Modal>
      )}
    </Page>
  );
}
