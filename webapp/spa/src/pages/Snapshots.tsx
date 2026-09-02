import { Select } from "../ui/Select";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { RuleEntry, SnapshotDate, SnapshotFile, SnapshotPage } from "../api/types";
import { Page } from "../layout/Page";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Empty, useBusy } from "../ui/badges";
import { useLightbox } from "../ui/Lightbox";

const PAGE_SIZE = 200;

interface SnapFilter {
  from: string;
  to: string;
  rule: string;
}

const EMPTY_FILTER: SnapFilter = { from: "", to: "", rule: "" };

export default function SnapshotsPage() {
  const [rules, setRules] = useState<RuleEntry[]>([]);
  const [draft, setDraft] = useState<SnapFilter>(EMPTY_FILTER);
  const [applied, setApplied] = useState<SnapFilter>(EMPTY_FILTER);
  const [days, setDays] = useState<SnapshotDate[]>([]);
  const [files, setFiles] = useState<SnapshotFile[]>([]);
  const [total, setTotal] = useState(0);
  const [totalMb, setTotalMb] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();
  const { busy, wrap } = useBusy();
  const { showGallery } = useLightbox();

  const query = useCallback(async (f: SnapFilter, offset = 0) => {
    const p = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (f.from) p.set("from_date", f.from);
    if (f.to) p.set("to_date", f.to);
    if (f.rule) p.set("rule", f.rule);
    const data = await api<SnapshotPage>("/api/snapshots?" + p.toString());
    setDays(data.dates);
    setTotal(data.total);
    setTotalMb(data.total_size_mb);
    // 追加页可能与浏览中新增的快照交叠——按 url 去重后合并
    setFiles((prev) => {
      const base = offset === 0 ? [] : prev;
      const have = new Set(base.map((x) => x.url));
      return [...base, ...(data.files || []).filter((x) => !have.has(x.url))];
    });
    setLoaded(true);
  }, []);

  const apply = useCallback(
    (f: SnapFilter) => {
      setApplied(f);
      setDraft(f);
      query(f, 0).catch((e) => toast((e as Error).message, false));
    },
    [query, toast],
  );

  useEffect(() => {
    api<{ rules: RuleEntry[] }>("/api/rules")
      .then((r) => setRules(r.rules))
      .catch(() => {});
    query(EMPTY_FILTER, 0).catch((e) => toast((e as Error).message, false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadMore = wrap("more", async () => {
    await query(applied, files.length);
  });

  const cleanup = wrap("cleanup", async () => {
    // Use the browser's local calendar date; toISOString() would shift this
    // date around midnight for users outside UTC.
    const d0 = new Date(Date.now() - 30 * 86400000);
    const d = `${d0.getFullYear()}-${String(d0.getMonth() + 1).padStart(2, "0")}-${String(d0.getDate()).padStart(2, "0")}`;
    if (!(await confirm(`删除 ${d} 之前的所有快照目录？此操作不可恢复。`))) return;
    try {
      const r = await api<{ deleted_dirs: number }>("/api/snapshots/cleanup", {
        method: "POST",
        body: { before_date: d },
      });
      toast(`已删除 ${r.deleted_dirs} 个日期目录`);
      query(applied, 0).catch((e) => toast((e as Error).message, false));
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const remain = Math.max(total - files.length, 0);
  const pickedDay = applied.from && applied.from === applied.to ? applied.from : null;

  return (
    <Page
      title="快照库"
      subtitle="按日期 / 规则分区存储，超期自动清理"
      actions={
        <button className="danger" disabled={busy.cleanup} onClick={cleanup}>
          清理 30 天前
        </button>
      }
    >
      <div className="card">
        <div className="filter-bar">
          <span className="muted" style={{ fontSize: 12 }}>从</span>
          <input
            type="date"
            style={{ width: 148 }}
            value={draft.from}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
          />
          <span className="muted" style={{ fontSize: 12 }}>至</span>
          <input
            type="date"
            style={{ width: 148 }}
            value={draft.to}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
          />
          <Select
            style={{ minWidth: 190 }}
            value={draft.rule}
            onChange={(e) => setDraft({ ...draft, rule: e.target.value })}
          >
            <option value="">全部类型</option>
            {rules.map((r) => (
              <option key={r.id} value={r.name}>
                R{String(r.id).padStart(2, "0")} {r.name}
              </option>
            ))}
          </Select>
          <button className="mini" onClick={() => apply(draft)}>
            查询
          </button>
          <button className="mini ghost" onClick={() => apply(EMPTY_FILTER)}>
            重置
          </button>
          <span className="muted" style={{ marginLeft: "auto" }}>
            {total ? `共 ${total} 张 · ${totalMb} MB` : ""}
          </span>
        </div>
        <div className="card-title">
          <span>
            {days.length
              ? `${days.length} 天有快照 · 点击日期可快速定位`
              : "快照"}
          </span>
          <div className="date-strip">
            {days.length ? (
              days.map((d) => (
                <div
                  key={d.date}
                  className={"d" + (pickedDay === d.date ? " active" : "")}
                  title={`${d.count} 张 · ${d.size_mb} MB`}
                  onClick={() => apply({ ...applied, from: d.date, to: d.date })}
                >
                  <b>{d.date}</b>
                </div>
              ))
            ) : (
              <span className="muted">暂无快照</span>
            )}
          </div>
        </div>
        <div className="snap-grid">
          {files.length ? (
            files.map((f, i) => (
              <figure key={f.url} title={`${f.rule_dir}/${f.date}/${f.name} · ${f.size_kb}KB`}>
                <img
                  src={f.thumb}
                  loading="lazy"
                  decoding="async"
                  title="点击放大"
                  style={{ cursor: "zoom-in" }}
                  onClick={() =>
                    showGallery(
                      files.map((x) => ({
                        src: x.url,
                        title: `${x.rule_dir} · ${x.name} · ${x.size_kb}KB`,
                      })),
                      i,
                    )
                  }
                  alt={f.name}
                />
                <figcaption>
                  {f.date} · {f.rule_dir}
                </figcaption>
              </figure>
            ))
          ) : (
            <div style={{ gridColumn: "1/-1" }}>
              <Empty>{loaded ? "暂无符合条件的快照" : "加载中…"}</Empty>
            </div>
          )}
        </div>
        <div style={{ textAlign: "center", marginTop: 14 }}>
          {remain > 0 ? (
            <button className="ghost" disabled={busy.more} onClick={loadMore}>
              加载更多（还有 {remain} 张）
            </button>
          ) : null}
        </div>
      </div>
    </Page>
  );
}
