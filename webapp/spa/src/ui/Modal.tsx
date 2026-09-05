import { useId, useRef } from "react";
import { useDialog } from "./useDialog";
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
  const titleId = useId();
  useDialog(boxRef, true, onClose);

  return (
    <div
      className={"modal-mask open" + (tall ? " modal-mask-tall" : "")}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={"modal" + (tall ? " modal-tall" : "")}
        style={{ width }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={boxRef}
        tabIndex={-1}
      >
        <div className="modal-h">
          <h3 id={titleId}>{title}</h3>
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
