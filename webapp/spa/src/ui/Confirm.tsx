import { createContext, useContext, useEffect, useRef, useState } from "react";

/* Promise 化确认弹窗（对齐旧 confirmDialog）：const ok = await confirm("...")
 * 键盘：Esc/遮罩点击=取消，Enter=确认；打开时焦点移到取消（danger）或确认按钮，
 * 关闭后归还焦点给触发元素，避免 Enter 二次触发。 */

interface ConfirmState {
  message: string;
  danger: boolean;
  okText: string;
  resolve: (v: boolean) => void;
}

const empty: ConfirmState = {
  message: "",
  danger: true,
  okText: "确认",
  resolve: () => {},
};

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
  const lastFocus = useRef<HTMLElement | null>(null);

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
      lastFocus.current = document.activeElement as HTMLElement | null;
      setSt(next);
    });

  const done = (v: boolean) => {
    st?.resolve(v);
    cur.current = null;
    setSt(null);
    lastFocus.current?.focus?.();
    lastFocus.current = null;
  };

  useEffect(() => {
    if (!st) return;
    // danger 操作默认焦点在取消上，防回车误确认
    (st.danger ? cancelRef.current : okRef.current)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        done(false);
      } else if (e.key === "Enter") {
        e.preventDefault();
        done(true);
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st]);

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
          <div className="modal" style={{ width: 400 }} role="alertdialog" aria-modal="true">
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
