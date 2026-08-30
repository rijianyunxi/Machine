import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { SnapshotDate, SnapshotFile } from "../api/types";
import { Page } from "../layout/Page";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { Empty, useBusy } from "../ui/badges";
import { useLightbox } from "../ui/Lightbox";

const PAGE_SIZE = 200;

export default function SnapshotsPage() {
  const [dates, setDates] = useState<SnapshotDate[]>([]);
  const [current, setCurrent] = useState<string | null>(null);
  const [files, setFiles] = useState<SnapshotFile[]>([]);
  const [total, setTotal] = useState(0);
  const [dayInfo, setDayInfo] = useState<{ total: number; size_mb: number } | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const { busy, wrap } = useBusy();
  const { showGallery } = useLightbox();

  const pick = useCallback(
    async (date: string, silent = false, offset = 0) => {
      const data = await api<{ dates: SnapshotDate[] }>(
        `/api/snapshots?date=${encodeURIComponent(date)}&offset=${offset}`,
      );
      const d = data.dates[0];
      if (!d) return;
      setCurrent(date);
      setDayInfo({ total: d.total ?? 0, size_mb: d.size_mb });
      setTotal(d.total ?? 0);
      if (offset === 0) {
        setFiles(d.files || []);
      } else {
        // 当天列表在浏览时还会增长——按文件名去重后追加
        setFiles((prev) => {
          const have = new Set(prev.map((f) => f.name));
          return [...prev, ...(d.files || []).filter((f) => !have.has(f.name))];
        });
      }
      if (!silent) refresh();
    },
    [],
  );

  const refresh = useCallback(async () => {
    const data = await api<{ dates: SnapshotDate[] }>("/api/snapshots");
    setDates(data.dates);
    const active = data.dates.find((d) => d.date === current) || data.dates[0];
    if (active && active.date !== current) pick(active.date, true);
    else if (active) setCurrent(active.date);
  }, [current, pick]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadMore = (btn: HTMLButtonElement | null) => {
    if (!current) return;
    void (async () => {
      try {
        await pick(current, true, files.length);
      } finally {
        if (btn) btn.disabled = false;
      }
    })();
  };

  const cleanup = wrap("cleanup", async () => {
    const d = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
    if (!(await confirm(`删除 ${d} 之前的所有快照目录？此操作不可恢复。`))) return;
    try {
      const r = await api<{ deleted_dirs: number }>("/api/snapshots/cleanup", {
        method: "POST",
        body: { before_date: d },
      });
      toast(`已删除 ${r.deleted_dirs} 个日期目录`);
      setCurrent(null);
      setFiles([]);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const remain = total - files.length;

  return (
    <Page
      title="快照库"
      subtitle="按日期 / 规则分区存储，超期自动清理"
      actions={
        <button className="ghost" disabled={busy.cleanup} onClick={cleanup}>
          清理 30 天前
        </button>
      }
    >
      <div className="card">
        <div className="card-title">
          <span>
            {current ? `${current} · ${dayInfo?.total ?? 0} 张 · ${dayInfo?.size_mb ?? 0} MB` : "快照"}
          </span>
          <div className="date-strip">
            {dates.length ? (
              dates.map((d) => (
                <div
                  key={d.date}
                  className={"d" + (current === d.date ? " active" : "")}
                  title={`${d.count} 张 · ${d.size_mb} MB`}
                  onClick={() => pick(d.date)}
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
              <figure key={f.name} title={`${f.name} · ${f.size_kb}KB`}>
                <img
                  src={f.thumb}
                  loading="lazy"
                  decoding="async"
                  title="点击放大"
                  style={{ cursor: "zoom-in" }}
                  onClick={() =>
                    showGallery(
                      files.map((x) => ({ src: x.url, title: `${x.name} · ${x.size_kb}KB` })),
                      i,
                    )
                  }
                  alt={f.name}
                />
                <figcaption>{f.name}</figcaption>
              </figure>
            ))
          ) : (
            <div style={{ gridColumn: "1/-1" }}>
              <Empty>该日期暂无快照</Empty>
            </div>
          )}
        </div>
        <div style={{ textAlign: "center", marginTop: 14 }}>
          {remain > 0 ? (
            <button className="ghost" onClick={(e) => loadMore(e.currentTarget)}>
              加载更多（还有 {remain} 张）
            </button>
          ) : null}
        </div>
      </div>
    </Page>
  );
}
