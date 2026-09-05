import { Select } from "../ui/Select";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { DatasetInfo } from "../api/types";
import { Page } from "../layout/Page";
import { Icon } from "../layout/icons";
import { useToast } from "../ui/Toast";
import { useBusy } from "../ui/badges";

/* 在线标注：拖拽画框（YOLO 归一化 cx/cy/w/h）· 快捷键 1-9 选类 / Del 删框 /
 * ←→ 切图（自动保存）/ Ctrl+S 保存 · 本地 YOLO 预标注 · LLM AI 识别 */

interface Box {
  cls: number;
  x: number; // 中心点归一化
  y: number;
  w: number;
  h: number;
  _ai?: boolean;
  _aiChecked?: boolean;
}

type DatasetSplit = "train" | "val" | "test";

interface ImageItem {
  file: string;
  stem: string;
  split: DatasetSplit;
  labeled: boolean;
}

type SplitFilter = "all" | DatasetSplit;

const SPLIT_LABELS: Record<DatasetSplit, string> = {
  train: "训练集",
  val: "验证集",
  test: "测试集",
};

const imageKey = (im: Pick<ImageItem, "file" | "split">) =>
  im.split + ":" + im.file;

const clsColor = (i: number) => `hsl(${Math.round((i * 137.508) % 360)}, 68%, 58%)`;

function AnnotationEmpty({ icon, title, description, children }: {
  icon: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="anno-empty">
      <span className="anno-empty__icon" aria-hidden="true"><Icon name={icon} size={24} /></span>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}

export default function AnnotatePage() {
  const [searchParams] = useSearchParams();
  const [dsList, setDsList] = useState<DatasetInfo[]>([]);
  const [ds, setDs] = useState("");
  const [classes, setClasses] = useState<string[]>([]);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [datasetLoading, setDatasetLoading] = useState(true);
  const [datasetError, setDatasetError] = useState("");
  const [idx, setIdx] = useState(-1);
  const [splitFilter, setSplitFilter] = useState<SplitFilter>("all");
  const [boxes, setBoxes] = useState<Box[]>([]);
  const [curCls, setCurCls] = useState(0);
  const [sel, setSel] = useState(-1);
  const [boxTab, setBoxTab] = useState<"manual" | "ai">("manual");
  const [ghost, setGhost] = useState<Box | null>(null);
  const [imgRect, setImgRect] = useState({ w: 0, h: 0 });
  const [stageLoading, setStageLoading] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [promptOpen, setPromptOpen] = useState(false);
  const dirtyRef = useRef(false);
  const boxesRef = useRef<Box[]>([]);
  boxesRef.current = boxes;
  const imgRef = useRef<HTMLImageElement>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const toast = useToast();
  const { busy, wrap } = useBusy();

  const img = idx >= 0 && idx < images.length ? images[idx] : null;
  const visibleImages =
    splitFilter === "all"
      ? images
      : images.filter((im) => im.split === splitFilter);
  const visibleIdx = img
    ? visibleImages.findIndex((im) => imageKey(im) === imageKey(img))
    : -1;
  const imgUrl = (im: Pick<ImageItem, "file" | "split">) =>
    `/api/datasets/${encodeURIComponent(ds)}/image/${encodeURIComponent(im.file)}?split=${encodeURIComponent(im.split)}`;
  const labelsUrl = (im: Pick<ImageItem, "stem" | "split">) =>
    `/api/datasets/${encodeURIComponent(ds)}/labels/${encodeURIComponent(im.stem)}?split=${encodeURIComponent(im.split)}`;

  const markDirty = () => {
    dirtyRef.current = true;
  };

  /* ---------------- 数据加载 ---------------- */

  const loadInfo = useCallback(async (name: string) => {
    const info = await api<{ classes: string[] }>(
      `/api/datasets/${encodeURIComponent(name)}`,
    );
    setClasses(info.classes);
  }, []);

  const saveLabels = useCallback(
    async (silent = false) => {
      const im = img;
      if (!im || !dirtyRef.current) return;
      const manual = boxes.filter((b) => !b._ai); // AI 建议框不保存
      await api(
        labelsUrl(im),
        { method: "PUT", body: { boxes: manual } },
      );
      dirtyRef.current = false;
      setImages((list) =>
        list.map((x) =>
          x.stem === im.stem && x.split === im.split
            ? { ...x, labeled: manual.length > 0 }
            : x,
        ),
      );
      if (!silent) toast(`已保存 ${manual.length} 个框`);
    },
    [boxes, ds, img, toast],
  );

  const jump = useCallback(
    async (i: number, keepDirty = false) => {
      if (dirtyRef.current && !keepDirty) await saveLabels(true);
      let next = i;
      if (next < 0 || next >= images.length) next = images.length ? 0 : -1;
      if (next < 0) return;
      setIdx(next);
      setSel(-1);
      const im = images[next];
      const data = await api<{ boxes: Box[] }>(
        labelsUrl(im),
      );
      setBoxes(data.boxes || []);
      dirtyRef.current = false;
    },
    [ds, images, saveLabels],
  );

  const nav = useCallback(
    (d: number) => {
      if (!visibleImages.length) return;
      const nextVisibleIdx =
        visibleIdx < 0
          ? 0
          : Math.min(Math.max(visibleIdx + d, 0), visibleImages.length - 1);
      const nextImage = visibleImages[nextVisibleIdx];
      const next = images.findIndex((im) => imageKey(im) === imageKey(nextImage));
      if (next >= 0 && next !== idx) void jump(next);
    },
    [idx, images, jump, visibleImages, visibleIdx],
  );

  const switchDs = useCallback(
    async (name: string) => {
      setDs(name);
      setDatasetLoading(true);
      setDatasetError("");
      setSplitFilter("all");
      setIdx(-1);
      setImages([]);
      setBoxes([]);
      setSel(-1);
      dirtyRef.current = false;
      try {
        await loadInfo(name);
        const r = await api<{ images: ImageItem[] }>(
          `/api/datasets/${encodeURIComponent(name)}/images`,
        );
        setImages(r.images);
        if (r.images.length) {
          const data = await api<{ boxes: Box[] }>(
            `/api/datasets/${encodeURIComponent(name)}/labels/${encodeURIComponent(r.images[0].stem)}?split=${encodeURIComponent(r.images[0].split)}`,
          );
          setIdx(0);
          setBoxes(data.boxes || []);
        }
      } catch (e) {
        setDatasetError((e as Error).message || "请检查连接后重试");
      } finally {
        setDatasetLoading(false);
      }
    },
    [loadInfo],
  );

  const changeSplitFilter = useCallback(
    async (nextFilter: SplitFilter) => {
      if (nextFilter === splitFilter) return;
      try {
        if (dirtyRef.current) await saveLabels(true);
        const nextImages =
          nextFilter === "all"
            ? images
            : images.filter((im) => im.split === nextFilter);
        setSplitFilter(nextFilter);
        if (!nextImages.length) {
          setIdx(-1);
          setSel(-1);
          setBoxes([]);
          dirtyRef.current = false;
          return;
        }
        const nextImage =
          img && nextImages.some((im) => imageKey(im) === imageKey(img))
            ? img
            : nextImages[0];
        const nextIndex = images.findIndex(
          (im) => imageKey(im) === imageKey(nextImage),
        );
        if (nextIndex >= 0) await jump(nextIndex, true);
      } catch (e) {
        toast((e as Error).message || "切换分区失败", false);
      }
    },
    [images, img, jump, saveLabels, splitFilter, toast],
  );

  useEffect(() => {
    (async () => {
      try {
        const list = (await api<{ datasets: DatasetInfo[] }>("/api/datasets")).datasets;
        setDsList(list);
        const initial = searchParams.get("ds") || list[0]?.name || "";
        if (initial) await switchDs(initial);
      } catch (e) {
        setDatasetError((e as Error).message || "请检查连接后重试");
      } finally {
        setDatasetLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------------- 类别 / 框操作 ---------------- */

  const setCls = (i: number) => {
    setCurCls(i);
    setBoxes((bs) => {
      if (sel >= 0 && bs[sel]) {
        const next = [...bs];
        next[sel] = { ...next[sel], cls: i };
        dirtyRef.current = true;
        return next;
      }
      return bs;
    });
  };

  const delBox = (i: number) => {
    setBoxes((bs) => bs.filter((_, bi) => bi !== i));
    setSel((s) => (s === i ? -1 : s));
    markDirty();
  };

  const setTab = (t: "manual" | "ai") => {
    setBoxTab(t);
    setSel((s) => {
      const cur = boxes[s];
      if (cur && (t === "ai") !== !!cur._ai) return -1; // 选区属于另一个 tab
      return s;
    });
  };

  /* 勾选的 AI 建议转正式框，未勾选的丢弃 */
  const promoteAi = () => {
    const keep = boxes.filter((b) => b._ai && b._aiChecked);
    if (!keep.length) {
      toast("先勾选要加入的建议框", false);
      return;
    }
    setBoxes((bs) =>
      bs
        .filter((b) => !b._ai || b._aiChecked)
        .map((b) => (b._ai ? { cls: b.cls, x: b.x, y: b.y, w: b.w, h: b.h } : b)),
    );
    markDirty();
    setSel(-1);
    setBoxTab("manual");
    toast(`已加入 ${keep.length} 个 AI 框（记得保存）`);
  };

  /* ---------------- 拖拽画框 / 移动 / 缩放 ---------------- */

  const dragRef = useRef<null | {
    mode: "draw" | "move" | "resize";
    x0?: number;
    y0?: number;
    i?: number;
    dx?: number;
    dy?: number;
    mx?: number;
    my?: number;
    bw?: number;
    bh?: number;
  }>(null);

  const norm = (e: MouseEvent | React.MouseEvent) => {
    const r = imgRef.current!.getBoundingClientRect();
    const px = Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1);
    const py = Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1);
    return { px, py };
  };

  const onStageMouseDown = (e: React.MouseEvent) => {
    if (e.target !== imgRef.current) return; // 只有图上起笔才画新框
    const { px, py } = norm(e);
    if (px < 0 || px > 1 || py < 0 || py > 1) return;
    dragRef.current = { mode: "draw", x0: px, y0: py };
    e.preventDefault();
  };

  const onBoxMouseDown = (e: React.MouseEvent, i: number) => {
    e.stopPropagation();
    e.preventDefault();
    setSel(i);
    const b = boxes[i];
    const target = e.target as HTMLElement;
    if (target.classList.contains("rz")) dragRef.current = { mode: "resize", i };
    else
      dragRef.current = {
        mode: "move",
        i,
        dx: b.x,
        dy: b.y,
        mx: e.clientX,
        my: e.clientY,
        bw: b.w,
        bh: b.h,
      };
  };

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag || !imgRef.current) return;
      const { px, py } = norm(e);
      if (drag.mode === "draw") {
        const x0 = Math.min(drag.x0!, px);
        const y0 = Math.min(drag.y0!, py);
        setGhost({
          cls: curCls,
          x: (x0 + px) / 2,
          y: (y0 + py) / 2,
          w: Math.abs(px - drag.x0!),
          h: Math.abs(py - drag.y0!),
        });
        return;
      }
      const i = drag.i!;
      if (drag.mode === "move") {
        setBoxes((bs) =>
          bs.map((b, bi) =>
            bi === i
              ? {
                  ...b,
                  x: Math.min(Math.max(drag.dx! + (e.clientX - drag.mx!) / imgRef.current!.getBoundingClientRect().width, b.w / 2), 1 - b.w / 2),
                  y: Math.min(Math.max(drag.dy! + (e.clientY - drag.my!) / imgRef.current!.getBoundingClientRect().height, b.h / 2), 1 - b.h / 2),
                }
              : b,
          ),
        );
      } else if (drag.mode === "resize") {
        setBoxes((bs) =>
          bs.map((b, bi) => {
            if (bi !== i) return b;
            const left = b.x - b.w / 2;
            const top = b.y - b.h / 2;
            return {
              ...b,
              w: Math.min(Math.max(px - left, 0.01), 1 - left),
              h: Math.min(Math.max(py - top, 0.01), 1 - top),
            };
          }),
        );
      }
      markDirty();
    };

    const onUp = () => {
      const drag = dragRef.current;
      if (!drag) return;
      if (drag.mode === "draw") {
        setGhost((g) => {
          if (g && g.w > 0.01 && g.h > 0.01) {
            setBoxes((bs) => [...bs, { cls: curCls, x: g.x, y: g.y, w: g.w, h: g.h }]);
            setSel(boxesRef.current.length); // 新框追加在末尾
            markDirty();
            // 手画的框是标注框——切回标注框 tab
            setBoxTab((t) => (t !== "manual" ? "manual" : t));
          }
          return null;
        });
      } else {
        markDirty();
      }
      dragRef.current = null;
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [curCls]);

  /* 图片加载 / 窗口变化 → 重算覆盖层尺寸 */
  const measure = useCallback(() => {
    const el = imgRef.current;
    if (el && el.naturalWidth) setImgRect({ w: el.clientWidth, h: el.clientHeight });
  }, []);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  /* ---------------- 键盘快捷键 ---------------- */

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA") return;
      if (/^[1-9]$/.test(e.key)) {
        const i = +e.key - 1;
        if (i < classes.length) setCls(i);
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (sel >= 0) delBox(sel);
      } else if (e.key === "ArrowLeft") nav(-1);
      else if (e.key === "ArrowRight") nav(1);
      else if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        void saveLabels();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  /* ---------------- AI 辅助 ---------------- */

  const llmPrompt = useCallback(() => {
    const allowed = classes.map((c, i) => `${i}:${c}`).join(", ");
    return "你是工地安全行为标注助手。请检测图中的目标，只允许使用以下类别（label 必须逐字取自该列表，禁止编造其它类别）：" +
      allowed + "。仅输出一个 JSON 数组，不要输出任何解释：" +
      '[{"label":"类别名","x":0.5,"y":0.5,"w":0.2,"h":0.3}]' +
      "，x/y 为框中心点、w/h 为框宽高，均按图片尺寸归一化到 0~1。图中没有这些类别目标时输出 []。";
  }, [classes]);

  const imgSize = (im: Pick<ImageItem, "file" | "split">) =>
    new Promise<{ w: number; h: number }>((resolve) => {
      const i = new Image();
      i.onload = () => resolve({ w: i.naturalWidth, h: i.naturalHeight });
      i.src = imgUrl(im);
    });

  const classIdOf = (name: string) => {
    const i = classes.findIndex((c) => c.toLowerCase() === String(name).toLowerCase());
    return i >= 0 ? i : 0;
  };

  const aiPrelabel = wrap("prelabel", async () => {
    if (idx < 0) {
      toast("先选择图片", false);
      return;
    }
    const im = img!;
    setStageLoading("本地模型推理中…");
    try {
      const blob = await fetch(imgUrl(im)).then((r) => r.blob());
      const fd = new FormData();
      fd.append("image", blob, im.file);
      fd.append("conf", "0.4");
      const d = await api<{ detections: Array<{ bbox: number[]; class_name: string }> }>(
        "/api/detect/test",
        { method: "POST", body: fd },
      );
      const shape = await imgSize(im);
      const newBoxes: Box[] = d.detections.map((x) => {
        const [x1, y1, x2, y2] = x.bbox;
        return {
          cls: classIdOf(x.class_name),
          x: (x1 + x2) / 2 / shape.w,
          y: (y1 + y2) / 2 / shape.h,
          w: (x2 - x1) / shape.w,
          h: (y2 - y1) / shape.h,
        };
      });
      if (!newBoxes.length) {
        toast("未检出目标，已保留原标注", false);
        return;
      }
      await api(
        labelsUrl(im),
        { method: "PUT", body: { boxes: newBoxes } },
      );
      setBoxes(newBoxes);
      markDirty();
      toast(`AI 预标注完成：${newBoxes.length} 个框（记得保存）`);
    } catch (e) {
      toast((e as Error).message, false);
    } finally {
      setStageLoading(null);
    }
  });

  const rerunAi = wrap("llm", async () => {
    if (idx < 0) {
      toast("先选择图片", false);
      return;
    }
    const im = img!;
    const p = (prompt.trim() || llmPrompt()).trim();
    setStageLoading("AI 识别中…");
    let secs = 0;
    const timer = setInterval(() => {
      secs += 1;
      setStageLoading(`AI 识别中… ${secs}s`);
    }, 1000);
    try {
      const blob = await fetch(imgUrl(im)).then((r) => r.blob());
      const b64 = await new Promise<string>((res) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result as string);
        fr.readAsDataURL(blob);
      });
      const r = await api<{ boxes: Box[] }>("/api/llm/chat", {
        method: "POST",
        body: { prompt: p, image: b64, classes },
      });
      if (r.boxes?.length) {
        setBoxes((bs) => [
          ...bs.filter((b) => !b._ai), // 替换上一轮建议
          ...r.boxes.map((b) => ({ ...b, _ai: true, _aiChecked: true })),
        ]);
        setBoxTab("ai");
        setSel(-1);
        toast(`AI 识别出 ${r.boxes.length} 个框，勾选后「加入所选」`);
      } else {
        toast("AI 未返回可用框，可修改提示词后重试", false);
      }
    } catch (e) {
      toast((e as Error).message, false);
    } finally {
      clearInterval(timer);
      setStageLoading(null);
    }
  });

  /* 提示词：展开时预填默认；内容变化自适应高度 */
  useEffect(() => {
    if (promptOpen && !prompt.trim()) setPrompt(llmPrompt());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptOpen]);

  useEffect(() => {
    const ta = promptRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(Math.max(ta.scrollHeight, 64), 240) + "px";
  }, [prompt, promptOpen]);

  /* ---------------- 渲染 ---------------- */

  const manual = boxes.map((b, i) => ({ b, i })).filter((x) => !x.b._ai);
  const ai = boxes.map((b, i) => ({ b, i })).filter((x) => x.b._ai);
  const shown = boxTab === "ai" ? ai : manual;
  const aiChecked = ai.filter((x) => x.b._aiChecked).length;

  return (
    <Page
      title="在线标注"
      subtitle="画框标注 · 快捷键 1-9 选类别 / Del 删除 / ←→ 切图（自动保存）/ Ctrl+S 保存"
      actions={
        <>
          <Select
            style={{ minWidth: 180 }}
            aria-label="选择数据集"
            disabled={datasetLoading}
            value={ds}
            onChange={(e) => switchDs(e.target.value)}
          >
            {!dsList.length && <option value="">暂无数据集</option>}
            {dsList.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name}
              </option>
            ))}
          </Select>
          <button className="ghost" disabled={busy.prelabel || !img} onClick={aiPrelabel}>
            <Icon name="zap" size={13} /> YOLO 预标注
          </button>
          <button className="ghost" disabled={busy.llm || !img} onClick={rerunAi}>
            <Icon name="sparkles" size={13} /> AI 识别
          </button>
          <button disabled={!img} onClick={() => saveLabels()}>保存 (Ctrl+S)</button>
        </>
      }
    >
      <div className="anno-layout" style={{ display: "grid", gridTemplateColumns: "250px 1fr 270px", gap: 14 }}>
        {/* 图片列表 */}
        <div className="card anno-list-card" style={{ padding: 12 }}>
          <div className="anno-list-head">
            <div className="card-title">
              图片 <span className="muted">{visibleImages.length}</span>
            </div>
            <Select
              aria-label="图片分区筛选"
              disabled={datasetLoading || !images.length}
              value={splitFilter}
              onChange={(e) => void changeSplitFilter(e.target.value as SplitFilter)}
            >
              <option value="all">全部分区</option>
              <option value="train">{SPLIT_LABELS.train}</option>
              <option value="val">{SPLIT_LABELS.val}</option>
              <option value="test">{SPLIT_LABELS.test}</option>
            </Select>
          </div>
          <div className="snap-grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {visibleImages.length ? (
              visibleImages.map((im) => {
                const actualIndex = images.findIndex((item) => imageKey(item) === imageKey(im));
                return (
                <figure key={`${im.split}:${im.file}`} style={{ margin: 0, cursor: "pointer" }} onClick={() => jump(actualIndex)}>
                  <img
                    src={imgUrl(im)}
                    loading="lazy"
                    alt={im.file}
                    style={{
                      width: "100%",
                      aspectRatio: "4/3",
                      objectFit: "cover",
                      borderRadius: 6,
                      border: `2px solid ${actualIndex === idx ? "var(--yellow)" : "var(--border)"}`,
                    }}
                  />
                  <figcaption
                    style={{
                      fontSize: 10,
                      marginTop: 2,
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span
                      className="mono"
                      style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    >
                      {im.file.slice(0, 12)}
                    </span>
                    <span
                      title={im.labeled ? "已标注" : "未标注"}
                      style={{
                        width: 10, height: 10, borderRadius: "50%", flexShrink: 0,
                        background: im.labeled ? "var(--green)" : "transparent",
                        border: im.labeled ? "none" : "2px solid var(--border-strong)",
                      }}
                    />
                  </figcaption>
                </figure>
                );
              })
            ) : (
              <AnnotationEmpty
                icon="images"
                title={datasetLoading ? "正在加载图片" : datasetError ? "图片加载失败" : images.length ? "当前分区暂无图片" : "暂无图片"}
                description={datasetLoading ? "请稍候，正在读取数据集。" : datasetError ? "请在画布区域重试。" : images.length ? "切换分区，查看其他图片。" : "导入图片后，将在这里显示缩略图。"}
              >
                {!!images.length && !datasetLoading && !datasetError && (
                  <button className="ghost" onClick={() => void changeSplitFilter("all")}>查看全部分区</button>
                )}
              </AnnotationEmpty>
            )}
          </div>
        </div>

        {/* 画布 */}
        <div className="card anno-canvas-card" style={{ padding: 14 }}>
          <div
            id="stage-wrap"
            className={`anno-stage${!img ? " anno-stage--empty" : ""}`}
            style={{ position: "relative", userSelect: "none", lineHeight: 0 }}
            onMouseDown={onStageMouseDown}
          >
            {img ? (
              <div className="anno-stage-inner">
                <img
                  ref={imgRef}
                  className="thumb"
                  src={imgUrl(img)}
                  alt={img.file}
                  style={{
                    aspectRatio: "auto",
                    maxHeight: "calc(100vh - 244px)",
                    width: "auto",
                    maxWidth: "100%",
                    cursor: "crosshair",
                  }}
                  onLoad={measure}
                />
                <div
                  className="anno-layer"
                  style={{
                    left: 0,
                    top: 0,
                    width: imgRect.w || undefined,
                    height: imgRect.h || undefined,
                  }}
                >
                  {shown.map(({ b, i }) => {
                    const x = (b.x - b.w / 2) * 100;
                    const y = (b.y - b.h / 2) * 100;
                    const name = classes[b.cls] ?? `cls${b.cls}`;
                    const col = clsColor(b.cls);
                    const tagInside = (b.y - b.h / 2) * imgRect.h < 22;
                    return (
                      <div
                        key={i}
                        className={`anno-box ${b._ai ? "llm" : ""} ${i === sel ? "sel" : ""}`}
                        style={{
                          left: `${x}%`,
                          top: `${y}%`,
                          width: `${b.w * 100}%`,
                          height: `${b.h * 100}%`,
                          borderColor: col,
                          opacity: b._ai && !b._aiChecked ? 0.3 : undefined,
                        }}
                        onMouseDown={(e) => onBoxMouseDown(e, i)}
                      >
                        <span
                          className="tag"
                          style={{ background: col, ...(tagInside ? { top: 2 } : {}) }}
                        >
                          {b.cls}:{name}
                        </span>
                        <span className="rz" />
                      </div>
                    );
                  })}
                  {ghost ? (
                    <div
                      className="anno-ghost"
                      style={{
                        left: `${(ghost.x - ghost.w / 2) * 100}%`,
                        top: `${(ghost.y - ghost.h / 2) * 100}%`,
                        width: `${ghost.w * 100}%`,
                        height: `${ghost.h * 100}%`,
                      }}
                    />
                  ) : null}
                </div>
                {stageLoading ? (
                  <div className="stage-loading">
                    <div style={{ textAlign: "center" }}>
                      <div className="spinner" />
                      <div className="lv" style={{ fontSize: 12.5 }}>
                        {stageLoading}
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <AnnotationEmpty
                icon={datasetError ? "alert-triangle" : "images"}
                title={datasetLoading ? "正在准备标注画布" : datasetError ? "暂时无法加载图片" : !ds ? "创建数据集，开始标注" : !images.length ? "导入第一张图片，开始标注" : "选择图片，开始标注"}
                description={datasetLoading ? "图片加载完成后即可开始标注。" : datasetError || (!images.length ? "前往数据集页导入图片，再回到这里选择类别、拖拽画框。" : "从图片列表选择一张图片；当前分区为空时，可切换到其他分区。")}
              >
                {!datasetLoading && (datasetError ? (
                  <button onClick={() => ds ? void switchDs(ds) : window.location.reload()}><Icon name="refresh" size={16} />重新加载</button>
                ) : !images.length ? (
                  <Link className="btn" to="/datasets"><Icon name="upload" size={16} />{ds ? "前往导入图片" : "前往创建数据集"}</Link>
                ) : null)}
              </AnnotationEmpty>
            )}
          </div>
          <div className="toolbar" style={{ marginTop: 12, justifyContent: "space-between" }}>
            <span className="muted">{img ? img.file : "标注画布"}</span>
            <span className="toolbar">
              <button className="mini ghost" disabled={visibleIdx <= 0} onClick={() => nav(-1)}>
                <Icon name="chevron-left" size={13} /> 上一张
              </button>
              <span className="muted">
                {visibleImages.length ? `${visibleIdx + 1}/${visibleImages.length}` : "0/0"}
              </span>
              <button className="mini ghost" disabled={visibleIdx < 0 || visibleIdx >= visibleImages.length - 1} onClick={() => nav(1)}>
                下一张 <Icon name="chevron-right" size={13} />
              </button>
            </span>
          </div>
        </div>

        {/* 类别与框列表 */}
        <div className="stack anno-side">
          <div className="card" style={{ padding: 14 }}>
            <div className="card-title" style={{ marginBottom: 8 }}>
              类别（按数字键选择）
            </div>
            <div
              className="inline-checks"
              style={{ flexDirection: "column", alignItems: "flex-start", gap: 6 }}
            >
              {classes.map((c, i) => (
                <div
                  key={i}
                  className={`cls-row ${i === curCls ? "active" : ""}`}
                  onClick={() => setCls(i)}
                >
                  <span className="cls-dot" style={{ background: clsColor(i) }} />
                  <span className="chip plain" style={{ fontSize: 10.5 }}>
                    {i}
                  </span>
                  <span>{c}</span>
                  {i === curCls ? (
                    <span style={{ marginLeft: "auto", color: "var(--accent)", display: "inline-flex" }}>
                      <Icon name="check" size={13} />
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
          <div className="card box-card" style={{ padding: 14 }}>
            <div className="anno-tabs">
              <button
                className={`tab ${boxTab === "manual" ? "on" : ""}`}
                onClick={() => setTab("manual")}
              >
                标注框 {manual.length}
              </button>
              <button
                className={`tab ${boxTab === "ai" ? "on" : ""}`}
                onClick={() => setTab("ai")}
              >
                AI 识别 {ai.length}
              </button>
            </div>
            <div className="feed" style={{ flex: 1, minHeight: 0, maxHeight: "none" }}>
              {boxTab === "ai" ? (
                ai.length ? (
                  ai.map(({ b, i }) => {
                    const name = classes[b.cls] ?? `cls${b.cls}`;
                    return (
                      <div
                        key={i}
                        className={`llm-pv ${b._aiChecked ? "" : "dimmed"} ${i === sel ? "sel" : ""}`}
                        onClick={() => setSel(i)}
                      >
                        <input
                          type="checkbox"
                          checked={!!b._aiChecked}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) =>
                            setBoxes((bs) =>
                              bs.map((x, xi) =>
                                xi === i ? { ...x, _aiChecked: e.target.checked } : x,
                              ),
                            )
                          }
                        />
                        <span className="cls-dot" style={{ background: clsColor(b.cls) }} />
                        <span>{name}</span>
                        <span className="muted mono" style={{ marginLeft: "auto" }}>
                          {(b.w * 100).toFixed(0)}×{(b.h * 100).toFixed(0)}
                        </span>
                      </div>
                    );
                  })
                ) : (
                  <AnnotationEmpty icon="sparkles" title={img ? "暂无 AI 识别结果" : "等待选择图片"} description={img ? "编辑提示词并点击「重新识别」，确认结果后加入标注。" : "选择图片后，可使用 AI 辅助识别目标。"} />
                )
              ) : manual.length ? (
                manual.map(({ b, i }) => (
                  <div
                    key={i}
                    className="item"
                    style={{ cursor: "pointer", color: i === sel ? "var(--yellow)" : undefined }}
                    onClick={() => setSel(i)}
                  >
                    <span className="cls-dot" style={{ background: clsColor(b.cls) }} />
                    <span>{classes[b.cls] ?? "cls" + b.cls}</span>
                    <span className="muted mono" style={{ marginLeft: "auto" }}>
                      {(b.w * 100).toFixed(0)}×{(b.h * 100).toFixed(0)}
                    </span>
                    <button
                      className="mini danger"
                      style={{ padding: "1px 7px", display: "inline-flex", alignItems: "center" }}
                      title="删除此框"
                      onClick={(e) => {
                        e.stopPropagation();
                        delBox(i);
                      }}
                    >
                      <Icon name="x" size={11} />
                    </button>
                  </div>
                ))
              ) : (
                <AnnotationEmpty
                  icon="square"
                  title={img ? "这张图片还没有标注" : "等待选择图片"}
                  description={img ? "先选择类别，再在图片上拖拽画框；完成后点击保存。" : "选择图片后，这里会显示标注框及对应类别。"}
                />
              )}
            </div>
            {boxTab === "ai" && ai.length ? (
              <button className="mini" style={{ width: "100%", marginTop: 8 }} onClick={promoteAi}>
                加入所选 ({aiChecked})
              </button>
            ) : null}
            {boxTab === "ai" ? (
              <div style={{ marginTop: 10 }}>
                <details
                  className="ai-prompt-details"
                  onToggle={(e) => setPromptOpen((e.target as HTMLDetailsElement).open)}
                >
                  <summary title="默认折叠，点开可修改提示词">
                    <span>提示词</span>
                    <span
                      className="mini ghost"
                      style={{ marginLeft: "auto", padding: "1px 8px" }}
                      onClick={(e) => {
                        e.preventDefault();
                        setPrompt(llmPrompt());
                      }}
                    >
                      恢复默认
                    </span>
                  </summary>
                  <textarea
                    ref={promptRef}
                    className="anno-prompt"
                    rows={4}
                    spellCheck={false}
                    value={prompt}
                    placeholder="告诉 AI 要检测什么目标、输出格式要求…"
                    onChange={(e) => setPrompt(e.target.value)}
                  />
                </details>
                <button
                  className="mini"
                  style={{ width: "100%", marginTop: 8 }}
                  disabled={busy.llm || !img}
                  onClick={rerunAi}
                >
                  <Icon name="refresh" size={12} /> 重新识别
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </Page>
  );
}
