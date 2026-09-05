import { useDialog } from "./useDialog";
import { createContext, useContext, useRef, useState } from "react";

/* Promise 化确认弹窗（对齐旧 confirmDialog）：const ok = await confirm("...")
 * 键盘：Esc/遮罩点击=取消，Enter=激活当前焦点按钮；打开时焦点移到取消（danger）或确认按钮，
 * 关闭后归还焦点给触发元素，避免 Enter 二次触发。 */

interface ConfirmState {
  message: string;
  danger: boolean;
  okText: string;
  resolve: (v: boolean) => void;
}

const ConfirmCtx = createContext<
  (message: string, opts?: { danger?: boolean; okText?: string }) => Promise<boolean>
>(async () => false);

export function useConfirm() {
  return useContext(ConfirmCtx);
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [st, setSt] = useState<ConfirmState | null>(null);
  const cur = useRef<ConfirmState | null>(null);
  const okRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const confirm = (
    message: string,
    opts?: { danger?: boolean; okText?: string },
  ) =>
    new Promise<boolean>((resolve) => {
      // 上一个未决的弹窗按取消处理
      cur.current?.resolve(false);
      const next = {
        message,
        danger: opts?.danger ?? true,
        okText: opts?.okText ?? "确认",
        resolve,
      };
      cur.current = next;
      setSt(next);
    });

  const done = (v: boolean) => {
    cur.current?.resolve(v);
    cur.current = null;
    setSt(null);
  };

  useDialog(boxRef, !!st, () => done(false), st?.danger ? cancelRef : okRef);

  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      {st && (
        <div
          id="confirm-root"
          className="modal-mask open"
          onClick={(e) => {
            if (e.target === e.currentTarget) done(false);
          }}
        >
          <div className="modal" style={{ width: 400 }} role="alertdialog" aria-modal="true" aria-label="操作确认" aria-describedby="confirm-msg" ref={boxRef} tabIndex={-1}>
            <div className="modal-b" style={{ paddingTop: 22 }}>
              <p id="confirm-msg" style={{ fontSize: 14, lineHeight: 1.7 }}>
                {st.message}
              </p>
            </div>
            <div className="modal-f">
              <button className="ghost" ref={cancelRef} onClick={() => done(false)}>
                取消
              </button>
              <button
                id="confirm-yes"
                className={st.danger ? "danger" : ""}
                ref={okRef}
                onClick={() => done(true)}
              >
                {st.okText}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmCtx.Provider>
  );
}
