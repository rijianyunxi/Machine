import { useEffect } from "react";

/* 通用弹窗骨架：遮罩 + 卡片，Esc 关闭。沿用 .modal-mask/.modal 样式。
 * tall：限高 + 内容区内部滚动 + 底部操作栏常驻（用于画布等高内容弹窗）。 */
export function Modal({
  title,
  width = 560,
  onClose,
  footer,
  children,
  tall,
}: {
  title: React.ReactNode;
  width?: number;
  onClose: () => void;
  footer?: React.ReactNode;
  children: React.ReactNode;
  tall?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="modal-mask open"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={"modal" + (tall ? " modal-tall" : "")} style={{ width }}>
        <div className="modal-h">
          <h3>{title}</h3>
          <button className="modal-x" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-b">{children}</div>
        {footer !== undefined ? (
          <div className="modal-f">{footer}</div>
        ) : null}
      </div>
    </div>
  );
}
