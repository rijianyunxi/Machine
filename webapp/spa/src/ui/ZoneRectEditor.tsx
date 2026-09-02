import { Select } from "./Select";
import { useEffect, useRef, useState } from "react";

/* 区域画框编辑器（自 Rules 页抽出，供规则参数区与画布编辑器共用）：
 * 取监控当前帧（无帧则 16:9 灰底），拖拽框选告警区域，
 * 归一化 x/y/w/h 存储（左上角原点）。 */
export function ZoneRectEditor({
  value,
  cameras,
  onChange,
}: {
  value: Array<Record<string, number>>;
  cameras: Array<{ id: string; name: string; connected?: boolean }>;
  onChange: (v: Array<Record<string, number>>) => void;
}) {
  const [camId, setCamId] = useState(cameras[0]?.id || "");
  const [ghost, setGhost] = useState<null | {
    x: number;
    y: number;
    w: number;
    h: number;
  }>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<null | { x0: number; y0: number }>(null);

  const norm = (e: MouseEvent | React.MouseEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    return {
      x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1),
      y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1),
    };
  };

  /* 拖拽全程挂 document：鼠标移出画布也能继续框选 */
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d || !stageRef.current) return;
      const { x, y } = norm(e);
      setGhost({
        x: Math.min(d.x0, x),
        y: Math.min(d.y0, y),
        w: Math.abs(x - d.x0),
        h: Math.abs(y - d.y0),
      });
    };
    const onUp = (e: MouseEvent) => {
      const d = dragRef.current;
      dragRef.current = null;
      if (!d || !stageRef.current) return;
      const { x, y } = norm(e);
      const w = Math.abs(x - d.x0);
      const h = Math.abs(y - d.y0);
      if (w > 0.02 && h > 0.02)
        onChange([
          ...value,
          { x: Math.min(d.x0, x), y: Math.min(d.y0, y), w, h },
        ]);
      setGhost(null);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  });

  const frameUrl = camId
    ? `/api/cameras/${encodeURIComponent(camId)}/frame.jpg?w=960`
    : "";

  return (
    <div className="zone-editor">
      <div style={{ padding: "8px 10px", display: "flex", gap: 10, alignItems: "center" }}>
        <span className="muted" style={{ fontSize: 11.5 }}>
          参考画面
        </span>
        <Select
          style={{ minWidth: 140 }}
          value={camId}
          onChange={(e) => setCamId(e.target.value)}
        >
          {cameras.length ? (
            cameras.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name || c.id}
              </option>
            ))
          ) : (
            <option value="">无可用监控</option>
          )}
        </Select>
        <span className="muted" style={{ fontSize: 11.5, marginLeft: "auto" }}>
          在画面上拖拽框选区域，共 {value.length} 个
        </span>
      </div>
      <div
        ref={stageRef}
        className="zone-stage"
        onMouseDown={(e) => {
          if (e.target !== stageRef.current && !(e.target as HTMLElement).classList.contains("zone-hint"))
            return;
          const { x, y } = norm(e);
          dragRef.current = { x0: x, y0: y };
          e.preventDefault();
        }}
      >
        {camId ? (
          <img
            src={frameUrl}
            alt=""
            draggable={false}
            onError={(e) => {
              (e.target as HTMLImageElement).style.visibility = "hidden";
            }}
          />
        ) : null}
        <div className="zone-hint">
          {camId ? "在画面上按住拖拽，框出告警区域" : "选择监控后取画面框选；或直接按画面比例框选"}
        </div>
        {value.map((z, i) => (
          <div
            key={i}
            className="zone-rect"
            style={{
              left: `${z.x * 100}%`,
              top: `${z.y * 100}%`,
              width: `${z.w * 100}%`,
              height: `${z.h * 100}%`,
            }}
          >
            <button
              type="button"
              className="zx"
              title="删除此区域"
              onClick={() => onChange(value.filter((_, zi) => zi !== i))}
            >
              ×
            </button>
          </div>
        ))}
        {ghost ? (
          <div
            className="zone-ghost"
            style={{
              left: `${ghost.x * 100}%`,
              top: `${ghost.y * 100}%`,
              width: `${ghost.w * 100}%`,
              height: `${ghost.h * 100}%`,
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
