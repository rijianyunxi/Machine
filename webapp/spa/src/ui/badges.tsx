import { useState } from "react";

/* 徽章 / 状态徽章 / 相机连接徽章 / 空状态 —— 类名沿用旧 app.css。 */

export function badge(text: string, color?: string) {
  return { text, color: color || "plain" };
}

export function Chip({
  text,
  color,
}: {
  text: React.ReactNode;
  color?: string;
}) {
  return <span className={`chip ${color || "plain"}`}>{text}</span>;
}

const STATUS_MAP: Record<string, [string, string]> = {
  new: ["新告警", "blue"],
  confirmed: ["确认违规", "red"],
  false_positive: ["误报", "yellow"],
  resolved: ["已处理", "green"],
};

export function StatusBadge({ status }: { status: string }) {
  const [t, c] = STATUS_MAP[status] || [status, "plain"];
  return <Chip text={t} color={c} />;
}

export function ConnectedBadge({
  cam,
}: {
  cam: { enabled: boolean; connected: boolean; thread_alive: boolean };
}) {
  if (!cam.enabled)
    return (
      <span className="chip plain">
        <span className="dot" />
        停用
      </span>
    );
  if (cam.connected)
    return (
      <span className="chip green">
        <span className="dot green pulse" />
        在线
      </span>
    );
  if (cam.thread_alive)
    return (
      <span className="chip yellow">
        <span className="dot yellow pulse" />
        重连中
      </span>
    );
  return (
    <span className="chip red">
      <span className="dot red" />
      离线
    </span>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="empty">
      <p>{children}</p>
    </div>
  );
}

/* 按钮异步动作的 busy 包装（对齐旧 withLoading 语义） */
export function useBusy() {
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const wrap = (key: string, fn: () => Promise<void>) => async () => {
    if (busy[key]) return;
    setBusy((b) => ({ ...b, [key]: true }));
    try {
      await fn();
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  };
  return { busy, wrap };
}
