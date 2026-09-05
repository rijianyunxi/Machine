import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { probe } from "../api/client";
import { NavIcon } from "./icons";

/* 页面骨架：左侧边栏（品牌 + 导航 + 系统状态）+ 右侧 Outlet。
 * 主题：index.html 首屏前已把 data-theme 写到 <html>，这里只负责切换与记忆。 */

type Theme = "light" | "dark";

function initialTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "dark"
    : "light";
}

const NAV_ITEMS: Array<{ key: string; label: string; to?: string }> = [
  { key: "dashboard", label: "总览", to: "/dashboard" },
  { key: "cameras", label: "监控管理", to: "/cameras" },
  { key: "models", label: "模型管理", to: "/models" },
  { key: "rules", label: "规则配置", to: "/rules" },
  { key: "detect", label: "检测测试台", to: "/detect" },
  { key: "alerts", label: "告警记录", to: "/alerts" },
  { key: "snapshots", label: "快照库", to: "/snapshots" },
  { key: "datasets", label: "数据集", to: "/datasets" },
  { key: "annotate", label: "在线标注", to: "/annotate" },
  { key: "train", label: "模型训练", to: "/train" },
  { key: "settings", label: "系统设置", to: "/settings" },
  { key: "logs", label: "日志", to: "/logs" },
];

export function AppLayout() {
  const [sys, setSys] = useState<{ cls: string; text: string }>({
    cls: "",
    text: "检测系统",
  });
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    probe("/api/system/info").then((ok) => {
      if (ok) setSys({ cls: "green", text: "检测系统运行中" });
      else setSys({ cls: "red", text: "API 不可达" });
    });
  }, []);

  const toggleTheme = () => {
    const next: Theme = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("panel-theme", next);
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="logo">
            <svg
              viewBox="0 0 24 24"
              width={18}
              height={18}
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
          </div>
          <div>
            <b>Machine</b>
            <span>安全行为检测面板</span>
          </div>
          <button
            className="theme-toggle"
            title={theme === "light" ? "切换为暗色主题" : "切换为亮色主题"}
            onClick={toggleTheme}
          >
            {theme === "light" ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
              </svg>
            )}
          </button>
        </div>
        <nav className="nav">
          {NAV_ITEMS.map((it) => (
            <NavLink
              key={it.key}
              to={it.to!}
              aria-label={it.label}
              title={it.label}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <NavIcon name={it.key} />
              <span>{it.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="mode">
            <span className={`dot ${sys.cls}`} id="sys-dot" />
            <span id="sys-mode">{sys.text}</span>
          </div>
        </div>
      </aside>
      <div className="main">
        <Outlet />
      </div>
    </div>
  );
}
