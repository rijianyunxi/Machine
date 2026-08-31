import { useEffect, useRef } from "react";
import { Icon } from "../layout/icons";

/* 通用弹窗骨架：遮罩 + 卡片，Esc 关闭。沿用 .modal-mask/.modal 样式。
 * tall：限高 + 内容区内部滚动 + 底部操作栏常驻（用于画布等高内容弹窗）。
 * 打开时焦点移入弹窗，关闭后归还，避免焦点残留触发底层按钮。 */
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
  const boxRef = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    lastFocus.current = document.activeElement as HTMLElement | null;
    // 焦点移入弹窗（优先第一个可聚焦控件，否则弹窗本身）
    const box = boxRef.current;
    if (box) {
      const focusable = box.querySelector<HTMLElement>(
        "button, input, select, textarea, [tabindex]:not([tabindex='-1'])",
      );
      (focusable || box).focus();
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      lastFocus.current?.focus?.();
    };
  }, [onClose]);

  return (
    <div
      className="modal-mask open"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={"modal" + (tall ? " modal-tall" : "")}
        style={{ width }}
        role="dialog"
        aria-modal="true"
        ref={boxRef}
        tabIndex={-1}
      >
        <div className="modal-h">
          <h3>{title}</h3>
          <button className="modal-x" onClick={onClose} aria-label="关闭">
            <Icon name="x" size={14} />
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
