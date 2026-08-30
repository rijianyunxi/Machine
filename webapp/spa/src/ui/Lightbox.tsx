import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

/* 图片灯箱（对齐旧 showGallery/showImageModal）：±1 邻图预加载、←→ 翻页、
 * Esc/点击遮罩关闭、加载 spinner。 */

export interface GalleryItem {
  src: string;
  title?: string;
}

interface GalleryState {
  items: GalleryItem[];
  index: number;
}

const LightboxCtx = createContext<{
  showImage: (src: string, title?: string) => void;
  showGallery: (items: GalleryItem[], index?: number) => void;
}>({ showImage: () => {}, showGallery: () => {} });

export function useLightbox() {
  return useContext(LightboxCtx);
}

export function LightboxProvider({ children }: { children: React.ReactNode }) {
  const [gal, setGal] = useState<GalleryState | null>(null);
  const [loading, setLoading] = useState(true);
  const idxRef = useRef(0);

  const showGallery = useCallback((items: GalleryItem[], index = 0) => {
    if (!items.length) return;
    idxRef.current = index;
    setLoading(true);
    setGal({ items, index });
  }, []);

  const showImage = useCallback(
    (src: string, title = "") => showGallery([{ src, title }], 0),
    [showGallery],
  );

  const nav = useCallback((delta: number) => {
    setGal((g) => {
      if (!g || g.items.length < 2) return g;
      const n = g.items.length;
      const index = (idxRef.current + delta + n) % n;
      idxRef.current = index;
      setLoading(true);
      return { ...g, index };
    });
  }, []);

  useEffect(() => {
    if (!gal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setGal(null);
      if (e.key === "ArrowLeft") nav(-1);
      if (e.key === "ArrowRight") nav(1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [gal, nav]);

  // 邻图预加载
  useEffect(() => {
    if (!gal || gal.items.length < 2) return;
    const n = gal.items.length;
    [idxRef.current - 1, idxRef.current + 1].forEach((i) => {
      const item = gal.items[(i + n) % n];
      if (item) new Image().src = item.src;
    });
  }, [gal]);

  const close = () => setGal(null);
  const many = !!gal && gal.items.length > 1;
  const item = gal?.items[gal.index];

  return (
    <LightboxCtx.Provider value={{ showImage, showGallery }}>
      {children}
      {gal && item && (
        <div id="img-modal" className="modal-mask img-modal open" onClick={close}>
          <figure className="img-lightbox">
            <button className="lightbox-x" title="关闭 (Esc)" onClick={close}>
              ✕
            </button>
            {many && (
              <button
                className="lb-arrow prev"
                title="上一张 (←)"
                onClick={(e) => {
                  e.stopPropagation();
                  nav(-1);
                }}
              >
                ‹
              </button>
            )}
            {many && (
              <button
                className="lb-arrow next"
                title="下一张 (→)"
                onClick={(e) => {
                  e.stopPropagation();
                  nav(1);
                }}
              >
                ›
              </button>
            )}
            <div className="lb-spinner" style={{ display: loading ? "" : "none" }} />
            <img
              alt="预览"
              src={item.src}
              onLoad={() => setLoading(false)}
              onClick={(e) => e.stopPropagation()}
            />
            <figcaption>
              {many
                ? `${item.title ? item.title + " · " : ""}${gal.index + 1} / ${gal.items.length}`
                : item.title || ""}
            </figcaption>
          </figure>
        </div>
      )}
    </LightboxCtx.Provider>
  );
}
