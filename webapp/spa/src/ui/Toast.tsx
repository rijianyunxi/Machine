import { createContext, useCallback, useContext, useRef, useState } from "react";
import { Icon } from "../layout/icons";

/* 右下角 toast 通知（沿用旧 #toast-root 样式）。 */

interface ToastItem {
  id: number;
  msg: string;
  ok: boolean;
}

const ToastCtx = createContext<(msg: string, ok?: boolean) => void>(() => {});

export function useToast() {
  return useContext(ToastCtx);
}

const MAX_TOASTS = 5;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const toast = useCallback((msg: string, ok = true) => {
    const id = ++seq.current;
    setItems((list) => [...list.slice(-MAX_TOASTS + 1), { id, msg, ok }]);
    setTimeout(() => {
      setItems((list) => list.filter((t) => t.id !== id));
    }, ok ? 2600 : 4500);
  }, []);

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div id="toast-root" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={"toast" + (t.ok ? "" : " err")} onClick={() => setItems((l) => l.filter((x) => x.id !== t.id))}>
            <Icon name={t.ok ? "check" : "x"} size={13} />
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
