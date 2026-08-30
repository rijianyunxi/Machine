import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DatasetInfo, PrelabelStatus } from "../api/types";
import { Page } from "../layout/Page";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { useLightbox } from "../ui/Lightbox";
import { Chip, Empty, useBusy } from "../ui/badges";

interface ImgInfo {
  file: string;
  stem: string;
  labeled: boolean;
  boxes: number;
}

const imgUrl = (ds: string, file: string) =>
  `/api/datasets/${encodeURIComponent(ds)}/image/${encodeURIComponent(file)}`;

export default function DatasetsPage() {
  const [datasets, setDatasets] = useState<DatasetInfo[] | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [cName, setCName] = useState("");
  const [cClasses, setCClasses] = useState("");
  const [snapTarget, setSnapTarget] = useState<string | null>(null);
  const [snapDate, setSnapDate] = useState("");
  const [snapLimit, setSnapLimit] = useState("200");
  const [pre, setPre] = useState<Record<string, PrelabelStatus>>({});
  const [mgr, setMgr] = useState<{ name: string; images: ImgInfo[] } | null>(
    null,
  );
  const [sel, setSel] = useState<Set<string>>(new Set());
  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const toast = useToast();
  const confirm = useConfirm();
  const lightbox = useLightbox();
  const { busy, wrap } = useBusy();

  const refresh = useCallback(async () => {
    const r = await api<{ datasets: DatasetInfo[] }>("/api/datasets");
    setDatasets(r.datasets);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // AI 预标注进度轮询：有运行中的任务时 1.5s 一次
  useEffect(() => {
    const running = Object.entries(pre).filter(([, s]) => s.running);
    if (!running.length) return;
    const timer = setInterval(async () => {
      for (const [name] of running) {
        try {
          const s = await api<PrelabelStatus>(
            `/api/datasets/${encodeURIComponent(name)}/prelabel_status`,
          );
          setPre((m) => ({ ...m, [name]: s }));
          if (!s.running) {
            if (s.error) toast(s.error, false);
            else if (s.total) toast(`预标注完成：${s.done}/${s.total} 张`);
            refresh();
          }
        } catch {
          /* 下个周期重试 */
        }
      }
    }, 1500);
    return () => clearInterval(timer);
  }); // 每次渲染后按最新 pre 状态重挂

  const create = wrap("create", async () => {
    const classes = cClasses
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    try {
      await api("/api/datasets", { method: "POST", body: { name: cName.trim(), classes } });
      toast("已创建");
      setCreateOpen(false);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const uploadImages = async (name: string, files: FileList | null) => {
    if (!files?.length) return;
    const fd = new FormData();
    for (const f of files) fd.append("images", f);
    try {
      const d = await api<{ added: number }>(
        `/api/datasets/${encodeURIComponent(name)}/images`,
        { method: "POST", body: fd },
      );
      toast(`已导入 ${d.added} 张`);
      refresh();
    } catch (e) {
      toast((e as Error).message || "导入失败", false);
    }
  };

  const doImportSnap = wrap("snap", async () => {
    if (!snapTarget) return;
    try {
      const r = await api<{ imported: number }>(
        `/api/datasets/${encodeURIComponent(snapTarget)}/import_snapshots`,
        {
          method: "POST",
          body: { date: snapDate.trim() || null, limit: +snapLimit || 200 },
        },
      );
      toast(`已导入 ${r.imported} 张快照`);
      setSnapTarget(null);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const prelabel = async (name: string) => {
    try {
      await api(`/api/datasets/${encodeURIComponent(name)}/prelabel`, {
        method: "POST",
        body: { model: "", conf: 0.4, only_unlabeled: true, limit: 200 },
      });
      toast("AI 预标注已启动，可离开本页（后台执行）");
      setPre((m) => ({ ...m, [name]: { running: true, done: 0, total: 0 } }));
    } catch (e) {
      toast((e as Error).message, false);
    }
  };

  const del = async (name: string) => {
    if (
      !(await confirm(`删除数据集 ${name}？图片与标注将一并删除，不可恢复！`))
    )
      return;
    try {
      await api(`/api/datasets/${encodeURIComponent(name)}`, { method: "DELETE" });
      toast("已删除");
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  };

  // ---- 图片管理弹窗 ----
  const openMgr = async (name: string) => {
    setMgr({ name, images: [] });
    setSel(new Set());
    try {
      const r = await api<{ images: ImgInfo[] }>(
        `/api/datasets/${encodeURIComponent(name)}/images?limit=1000`,
      );
      setMgr({ name, images: r.images });
    } catch (e) {
      toast((e as Error).message || "图片列表加载失败", false);
      setMgr(null);
    }
  };

  const toggleSel = (file: string) => {
    setSel((s) => {
      const n = new Set(s);
      if (n.has(file)) n.delete(file);
      else n.add(file);
      return n;
    });
  };

  const delImages = async (files: string[]) => {
    if (!mgr || !files.length) return;
    if (
      !(await confirm(
        files.length === 1
          ? `删除图片 ${files[0]}？其标注将一并删除，不可恢复！`
          : `删除选中的 ${files.length} 张图片？标注将一并删除，不可恢复！`,
      ))
    )
      return;
    try {
      const r = await api<{ deleted: number }>(
        `/api/datasets/${encodeURIComponent(mgr.name)}/images`,
        { method: "DELETE", body: { filenames: files } },
      );
      toast(`已删除 ${r.deleted} 张`);
      const gone = new Set(files);
      setSel((s) => new Set([...s].filter((f) => !gone.has(f))));
      setMgr((m) =>
        m ? { ...m, images: m.images.filter((i) => !gone.has(i.file)) } : m,
      );
      refresh();
    } catch (e) {
      toast((e as Error).message || "删除失败", false);
    }
  };

  return (
    <Page
      title="数据集"
      subtitle="YOLO 格式数据集：新建 / 导入图片 / 从快照导入 / AI 批量预标注"
      actions={<button onClick={() => setCreateOpen(true)}>＋ 新建数据集</button>}
    >
      <div className="grid cards">
        {datasets === null ? (
          <div className="card" style={{ gridColumn: "1/-1" }}>
            <Empty>加载中…</Empty>
          </div>
        ) : datasets.length ? (
          datasets.map((d) => (
            <div className="card" key={d.name}>
              <div className="ds-head">
                <div className="ds-name">
                  <b title={d.name}>{d.name}</b>
                  <span className="muted">{d.images} 张图片</span>
                </div>
                {d.images ? (
                  <Chip
                    text={`${d.labeled}/${d.images} 已标`}
                    color={d.labeled === d.images ? "green" : "yellow"}
                  />
                ) : (
                  <Chip text="空数据集" />
                )}
              </div>
              <div className="ds-classes">
                <span className="lbl">类别 {d.classes.length}</span>
                {d.classes.length ? (
                  d.classes.map((c, i) => (
                    <Chip key={i} text={`${i}:${c}`} color="blue" />
                  ))
                ) : (
                  <span className="lbl">—</span>
                )}
              </div>
              <div className="toolbar" style={{ marginTop: 12 }}>
                <a className="btn mini" href={`/app/annotate?ds=${encodeURIComponent(d.name)}`}>
                  打开标注
                </a>
                <label className="btn ghost mini">
                  导入图片
                  <input
                    ref={(el) => {
                      fileRefs.current[d.name] = el;
                    }}
                    type="file"
                    multiple
                    accept=".jpg,.jpeg,.png,.webp"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      uploadImages(d.name, e.target.files);
                      e.target.value = "";
                    }}
                  />
                </label>
                <button className="mini ghost" onClick={() => setSnapTarget(d.name)}>
                  从快照导入
                </button>
                <button
                  className="mini ghost"
                  disabled={busy.pl}
                  onClick={() => prelabel(d.name)}
                >
                  {pre[d.name]?.running
                    ? `预标注 ${pre[d.name].done}/${pre[d.name].total}`
                    : "AI 预标注"}
                </button>
                <button
                  className="mini ghost"
                  disabled={!d.images}
                  onClick={() => openMgr(d.name)}
                >
                  管理图片
                </button>
                <button className="mini danger" onClick={() => del(d.name)}>
                  删除
                </button>
              </div>
            </div>
          ))
        ) : (
          <div className="card" style={{ gridColumn: "1/-1" }}>
            <Empty>还没有数据集，点右上角「新建数据集」开始</Empty>
          </div>
        )}
      </div>

      {createOpen && (
        <Modal
          title="新建数据集"
          width={480}
          onClose={() => setCreateOpen(false)}
          footer={
            <>
              <button className="ghost" onClick={() => setCreateOpen(false)}>
                取消
              </button>
              <button disabled={busy.create} onClick={create}>
                创建
              </button>
            </>
          }
        >
          <label>数据集名称（字母/数字/下划线/连字符）</label>
          <input
            className="w320"
            placeholder="site_ppe_v1"
            value={cName}
            onChange={(e) => setCName(e.target.value)}
          />
          <label>类别列表（逗号分隔，顺序即类别 ID：0,1,2…）</label>
          <input
            style={{ width: "100%" }}
            placeholder="person, no-hardhat, cigarette"
            value={cClasses}
            onChange={(e) => setCClasses(e.target.value)}
          />
          <p className="muted" style={{ marginTop: 10 }}>
            创建后可在「在线标注」页补充图片与标注。
          </p>
        </Modal>
      )}

      {snapTarget && (
        <Modal
          title="从快照导入"
          width={440}
          onClose={() => setSnapTarget(null)}
          footer={
            <>
              <button className="ghost" onClick={() => setSnapTarget(null)}>
                取消
              </button>
              <button disabled={busy.snap} onClick={doImportSnap}>
                导入
              </button>
            </>
          }
        >
          <p className="muted" style={{ marginBottom: 6 }}>
            导入到数据集：{snapTarget}
          </p>
          <label>日期（留空 = 最近日期优先）</label>
          <input
            className="w240"
            placeholder="2026-08-28"
            value={snapDate}
            onChange={(e) => setSnapDate(e.target.value)}
          />
          <label>最多导入张数</label>
          <input
            className="w240"
            type="number"
            value={snapLimit}
            onChange={(e) => setSnapLimit(e.target.value)}
          />
        </Modal>
      )}

      {mgr && (
        <Modal
          title={`管理图片 · ${mgr.name}`}
          width={880}
          onClose={() => setMgr(null)}
          footer={
            <>
              <button className="ghost" onClick={() => setMgr(null)}>
                关 闭
              </button>
              <button
                className="danger"
                disabled={!sel.size}
                onClick={() => delImages([...sel])}
              >
                删除选中{sel.size ? `（${sel.size}）` : ""}
              </button>
            </>
          }
        >
          <div className="img-mgr-bar">
            <span className="muted" style={{ fontSize: 12.5 }}>
              {mgr.images.length
                ? `共 ${mgr.images.length} 张 · 已标 ${
                    mgr.images.filter((i) => i.labeled).length
                  } · 已选 ${sel.size}`
                : "加载中…"}
            </span>
            {mgr.images.length > 0 && (
              <>
                <button
                  className="mini ghost"
                  onClick={() => setSel(new Set(mgr.images.map((i) => i.file)))}
                >
                  全选
                </button>
                <button
                  className="mini ghost"
                  disabled={!sel.size}
                  onClick={() => setSel(new Set())}
                >
                  清除选择
                </button>
              </>
            )}
          </div>
          {mgr.images.length ? (
            <div className="img-mgr-grid">
              {mgr.images.map((im) => (
                <figure
                  key={im.file}
                  className={"img-mgr" + (sel.has(im.file) ? " on" : "")}
                >
                  <input
                    className="pick"
                    type="checkbox"
                    title="选择"
                    checked={sel.has(im.file)}
                    onChange={() => toggleSel(im.file)}
                  />
                  <img
                    src={imgUrl(mgr.name, im.file)}
                    alt={im.file}
                    loading="lazy"
                    onClick={() =>
                      lightbox.showGallery(
                        mgr.images.map((i) => ({
                          src: imgUrl(mgr.name, i.file),
                          title: `${mgr.name}/${i.file}`,
                        })),
                        mgr.images.findIndex((i) => i.file === im.file),
                      )
                    }
                  />
                  <figcaption className="meta">
                    <span className="fname" title={im.file}>
                      {im.file}
                    </span>
                    {im.labeled ? (
                      <span className="chip green" style={{ padding: "1px 7px", fontSize: 10.5 }}>
                        {im.boxes}框
                      </span>
                    ) : (
                      <span className="chip plain" style={{ padding: "1px 7px", fontSize: 10.5 }}>
                        未标
                      </span>
                    )}
                    <button
                      className="mini danger"
                      style={{ padding: "1px 7px", fontSize: 11 }}
                      title="删除此图"
                      onClick={() => delImages([im.file])}
                    >
                      删
                    </button>
                  </figcaption>
                </figure>
              ))}
            </div>
          ) : (
            <Empty>该数据集还没有图片，可用上方「导入图片 / 从快照导入」</Empty>
          )}
          {mgr.images.length >= 1000 && (
            <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>
              仅显示前 1000 张（按时间先后），如需清理更多请先删除后再进入。
            </p>
          )}
        </Modal>
      )}
    </Page>
  );
}
