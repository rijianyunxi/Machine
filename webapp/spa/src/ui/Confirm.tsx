import { createContext, useContext, useRef, useState } from "react";

/* Promise 化确认弹窗（对齐旧 confirmDialog）：const ok = await confirm("...") */

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
    st?.resolve(v);
    cur.current = null;
    setSt(null);
  };

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
          <div className="modal" style={{ width: 400 }}>
            <div className="modal-b" style={{ paddingTop: 22 }}>
              <p id="confirm-msg" style={{ fontSize: 14, lineHeight: 1.7 }}>
                {st.message}
              </p>
            </div>
            <div className="modal-f">
              <button className="ghost" onClick={() => done(false)}>
                取消
              </button>
              <button
                id="confirm-yes"
                className={st.danger ? "danger" : ""}
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
