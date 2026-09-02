import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DatasetInfo, PrelabelStatus } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { Modal } from "../ui/Modal";
import { useConfirm } from "../ui/Confirm";
import { useToast } from "../ui/Toast";
import { useLightbox } from "../ui/Lightbox";
import { Chip, Empty, useBusy } from "../ui/badges";

type DatasetSplit = "train" | "val" | "test";

const SPLIT_LABELS: Record<DatasetSplit, string> = {
  train: "训练集",
  val: "验证集",
  test: "测试集",
};

interface ImgInfo {
  file: string;
  stem: string;
  split: DatasetSplit;
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
  const [uploadSplits, setUploadSplits] = useState<Record<string, DatasetSplit>>({});
  const [snapTarget, setSnapTarget] = useState<string | null>(null);
  const [snapSplit, setSnapSplit] = useState<DatasetSplit>("train");
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
  const [busyPl, setBusyPl] = useState(false);

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

  const uploadImages = async (
    name: string,
    files: FileList | null,
    split: DatasetSplit,
  ) => {
    if (!files?.length) return;
    const fd = new FormData();
    for (const f of files) fd.append("images", f);
    fd.append("split", split);
    try {
      const d = await api<{ added: number }>(
        `/api/datasets/${encodeURIComponent(name)}/images`,
        { method: "POST", body: fd },
      );
      toast(`已导入 ${d.added} 张到${SPLIT_LABELS[split]}`);
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
          body: {
            date: snapDate.trim() || null,
            limit: +snapLimit || 200,
            split: snapSplit,
          },
        },
      );
      toast(`已导入 ${r.imported} 张快照到${SPLIT_LABELS[snapSplit]}`);
      setSnapTarget(null);
      refresh();
    } catch (e) {
      toast((e as Error).message, false);
    }
  });

  const prelabel = async (name: string) => {
    if (busy.pl || pre[name]?.running) return;
    setBusyPl(true);
    try {
      await api(`/api/datasets/${encodeURIComponent(name)}/prelabel`, {
        method: "POST",
        body: { model: "", conf: 0.4, only_unlabeled: true, limit: 200 },
      });
      toast("AI 预标注已启动，可离开本页（后台执行）");
      setPre((m) => ({ ...m, [name]: { running: true, done: 0, total: 0 } }));
    } catch (e) {
      toast((e as Error).message, false);
    } finally {
      setBusyPl(false);
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

  const totals = (datasets ?? []).reduce(
    (acc, dataset) => ({
      images: acc.images + dataset.images,
      labeled: acc.labeled + dataset.labeled,
    }),
    { images: 0, labeled: 0 },
  );
  const overallProgress = totals.images
    ? Math.round((totals.labeled / totals.images) * 100)
    : 0;

  return (
    <Page
      title="数据集"
      subtitle="管理训练图片、类别与标注进度，支持快照导入和 AI 批量预标注"
      actions={
        <button onClick={() => setCreateOpen(true)}>
          <Icon name="plus" size={14} /> 新建数据集
        </button>
      }
    >
      {datasets !== null && datasets.length > 0 && (
        <section className="datasets-overview" aria-label="数据集概览">
          <div className="datasets-overview__item">
            <span className="datasets-overview__icon">
              <Icon name="database" size={18} />
            </span>
            <span>
              <small>数据集</small>
              <strong>{datasets.length}</strong>
            </span>
          </div>
          <div className="datasets-overview__item">
            <span className="datasets-overview__icon">
              <Icon name="images" size={18} />
            </span>
            <span>
              <small>图片总数</small>
              <strong>{totals.images}</strong>
            </span>
          </div>
          <div className="datasets-overview__item datasets-overview__item--progress">
            <span className="datasets-overview__icon datasets-overview__icon--green">
              <Icon name="check-square" size={18} />
            </span>
            <span>
              <small>总体标注进度</small>
              <strong>{overallProgress}%</strong>
              <em>{totals.labeled}/{totals.images} 张已标注</em>
            </span>
          </div>
        </section>
      )}

      <section className="datasets-grid" aria-label="数据集列表">
        {datasets === null ? (
          <div className="card datasets-state">
            <Empty>加载中…</Empty>
          </div>
        ) : datasets.length ? (
          datasets.map((d, datasetIndex) => {
            const progress = d.images
              ? Math.round((d.labeled / d.images) * 100)
              : 0;
            const visibleClasses = d.classes.slice(0, 4);
            const hiddenClassCount = d.classes.length - visibleClasses.length;
            const prelabelStatus = pre[d.name];

            return (
              <article
                className="dataset-card"
                key={d.name}
                aria-labelledby={`dataset-title-${datasetIndex}`}
              >
                <header className="dataset-card__header">
                  <div className="dataset-card__identity">
                    <span className="dataset-card__icon">
                      <Icon name="database" size={19} />
                    </span>
                    <div>
                      <h2 id={`dataset-title-${datasetIndex}`} title={d.name}>
                        {d.name}
                      </h2>
                      <p>{d.images} 张图片 · 训练 {d.splits?.train?.images ?? 0} · 验证 {d.splits?.val?.images ?? 0} · 测试 {d.splits?.test?.images ?? 0} · {d.classes.length} 个类别</p>
                    </div>
                  </div>
                  {d.images ? (
                    <Chip
                      text={d.labeled === d.images ? "标注完成" : "标注中"}
                      color={d.labeled === d.images ? "green" : "yellow"}
                    />
                  ) : (
                    <Chip text="空数据集" />
                  )}
                </header>

                <div className="dataset-card__progress">
                  <div className="dataset-card__progress-label">
                    <span>标注进度</span>
                    <strong>{d.labeled}/{d.images || 0} · {progress}%</strong>
                  </div>
                  <div
                    className="dataset-progress"
                    role="progressbar"
                    aria-label={`${d.name} 标注进度`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progress}
                  >
                    <span style={{ width: `${progress}%` }} />
                  </div>
                </div>

                <div className="dataset-card__classes">
                  <div className="dataset-card__section-title">
                    <span>类别</span>
                    <small>{d.classes.length}</small>
                  </div>
                  <div className="dataset-card__class-list">
                    {visibleClasses.length ? (
                      <>
                        {visibleClasses.map((className, classIndex) => (
                          <Chip
                            key={`${classIndex}-${className}`}
                            text={`${classIndex}:${className}`}
                            color="blue"
                          />
                        ))}
                        {hiddenClassCount > 0 && (
                          <span className="dataset-card__class-more">
                            +{hiddenClassCount}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="dataset-card__class-empty">尚未设置类别</span>
                    )}
                  </div>
                </div>

                <div className="dataset-card__actions">
                  <div className="dataset-card__primary-actions">
                    <a
                      className="btn dataset-action dataset-action--primary"
                      href={`/app/annotate?ds=${encodeURIComponent(d.name)}`}
                    >
                      <Icon name="edit" size={14} /> 打开标注
                    </a>
                    <div className="dataset-upload-action">
                      <select
                        className="dataset-split-select"
                        aria-label={`${d.name} 图片导入目标`}
                        value={uploadSplits[d.name] ?? "train"}
                        onChange={(e) =>
                          setUploadSplits((m) => ({
                            ...m,
                            [d.name]: e.target.value as DatasetSplit,
                          }))
                        }
                      >
                        <option value="train">训练集</option>
                        <option value="val">验证集</option>
                        <option value="test">测试集</option>
                      </select>
                      <button
                        className="ghost dataset-action dataset-action--secondary"
                        onClick={() => fileRefs.current[d.name]?.click()}
                      >
                        <Icon name="upload" size={14} /> 导入图片
                      </button>
                    </div>
                    <input
                      ref={(el) => {
                        fileRefs.current[d.name] = el;
                      }}
                      className="dataset-card__file-input"
                      type="file"
                      multiple
                      accept=".jpg,.jpeg,.png,.webp"
                      aria-label={`向 ${d.name} 导入图片`}
                      onChange={(e) => {
                        uploadImages(
                          d.name,
                          e.target.files,
                          uploadSplits[d.name] ?? "train",
                        );
                        e.target.value = "";
                      }}
                    />
                  </div>
                  <div className="dataset-card__secondary-actions">
                    <button
                      className="ghost dataset-action dataset-action--quiet"
                      onClick={() => {
                        setSnapTarget(d.name);
                        setSnapSplit("train");
                      }}
                    >
                      <Icon name="camera" size={13} /> 从快照导入
                    </button>
                    <button
                      className={`ghost dataset-action dataset-action--quiet${
                        prelabelStatus?.running ? " is-running" : ""
                      }`}
                      disabled={busyPl || !!prelabelStatus?.running}
                      onClick={() => prelabel(d.name)}
                    >
                      <Icon name="sparkles" size={13} />
                      {prelabelStatus?.running
                        ? prelabelStatus.total
                          ? `预标注 ${prelabelStatus.done}/${prelabelStatus.total}`
                          : "正在启动…"
                        : "AI 预标注"}
                    </button>
                    <button
                      className="ghost dataset-action dataset-action--quiet"
                      disabled={!d.images}
                      onClick={() => openMgr(d.name)}
                    >
                      <Icon name="folder" size={13} /> 管理图片
                    </button>
                    <button
                      className="danger dataset-action dataset-action--danger"
                      onClick={() => del(d.name)}
                    >
                      <Icon name="trash" size={13} /> 删除
                    </button>
                  </div>
                </div>
              </article>
            );
          })
        ) : (
          <div className="card datasets-state">
            <Empty>还没有数据集，点右上角「新建数据集」开始</Empty>
          </div>
        )}
      </section>

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
          <label>导入到</label>
          <select
            className="w240"
            value={snapSplit}
            onChange={(e) => setSnapSplit(e.target.value as DatasetSplit)}
          >
            <option value="train">训练集</option>
            <option value="val">验证集</option>
            <option value="test">测试集</option>
          </select>
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
          tall
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
                    <span className="chip plain split-chip">
                      {SPLIT_LABELS[im.split]}
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


