import { createContext, useCallback, useContext, useRef, useState } from "react";

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

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const toast = useCallback((msg: string, ok = true) => {
    const id = ++seq.current;
    setItems((list) => [...list, { id, msg, ok }]);
    setTimeout(() => {
      setItems((list) => list.filter((t) => t.id !== id));
    }, 2600);
  }, []);

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div id="toast-root">
        {items.map((t) => (
          <div key={t.id} className={"toast" + (t.ok ? "" : " err")}>
            <span>{t.ok ? "✓" : "✕"}</span>
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
