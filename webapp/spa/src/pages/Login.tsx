import { useState } from "react";
import { useNavigate } from "react-router-dom";

/* 登录表单：独立登录页与 401 弹窗复用同一组件。 */

export function LoginForm({
  onOk,
  onCancel,
  compact,
}: {
  onOk: () => void;
  onCancel?: () => void;
  compact?: boolean;
}) {
  const nav = useNavigate();
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy) return;
    setErr("");
    setBusy(true);
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user.trim(), password: pass }),
      });
      if (res.ok) {
        onOk();
        if (!compact) nav("/dashboard", { replace: true });
        return;
      }
      setErr((await res.json()).detail || "登录失败");
    } catch {
      setErr("登录失败");
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <>
      <label>用户名</label>
      <input
        style={{ width: "100%" }}
        placeholder="admin"
        autoComplete="username"
        value={user}
        onChange={(e) => setUser(e.target.value)}
      />
      <label>密码</label>
      <input
        style={{ width: "100%" }}
        type="password"
        placeholder="••••••••"
        autoComplete="current-password"
        value={pass}
        onChange={(e) => setPass(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
      />
      {err ? (
        <p className="muted" style={{ color: "var(--red)", marginTop: 10 }}>
          {err}
        </p>
      ) : null}
      <button
        style={compact ? { width: "100%", marginTop: 18 } : { width: "100%", marginTop: 18 }}
        onClick={submit}
        disabled={busy}
      >
        登 录
      </button>
      {compact && onCancel ? (
        <button
          className="ghost"
          style={{ width: "100%", marginTop: 8 }}
          onClick={onCancel}
        >
          取消
        </button>
      ) : null}
    </>
  );

  if (compact) return form;

  return (
    <div style={{ minHeight: "70vh", display: "grid", placeItems: "center" }}>
      <div className="card" style={{ width: 380, padding: 30 }}>
        <div style={{ textAlign: "center", marginBottom: 20 }}>
          <div
            className="logo"
            style={{
              width: 48,
              height: 48,
              borderRadius: 13,
              margin: "0 auto 12px",
              display: "grid",
              placeItems: "center",
              background:
                "linear-gradient(135deg,var(--accent),var(--accent-2))",
              color: "#04121f",
            }}
          >
            <svg
              viewBox="0 0 24 24"
              width={24}
              height={24}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
          </div>
          <h2 style={{ fontSize: 17 }}>Machine</h2>
          <p className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
            安全行为检测面板 · 请登录以继续
          </p>
        </div>
        {form}
      </div>
    </div>
  );
}
