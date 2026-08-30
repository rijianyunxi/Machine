import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { probe } from "../api/client";
import { NavIcon } from "./icons";

/* 页面骨架：左侧边栏（品牌 + 导航 + 系统状态）+ 右侧 Outlet。 */

const NAV_ITEMS: Array<{ key: string; label: string; to?: string }> = [
  { key: "dashboard", label: "总览", to: "/dashboard" },
  { key: "cameras", label: "监控管理", to: "/cameras" },
  { key: "models", label: "模型管理", to: "/models" },
  { key: "datasets", label: "数据集", to: "/datasets" },
  { key: "annotate", label: "在线标注", to: "/annotate" },
  { key: "train", label: "模型训练", to: "/train" },
  { key: "rules", label: "规则配置", to: "/rules" },
  { key: "detect", label: "检测测试台", to: "/detect" },
  { key: "alerts", label: "告警记录", to: "/alerts" },
  { key: "snapshots", label: "快照库", to: "/snapshots" },
  { key: "settings", label: "系统设置", to: "/settings" },
  { key: "logs", label: "日志", to: "/logs" },
];

export function AppLayout() {
  const [sys, setSys] = useState<{ cls: string; text: string }>({
    cls: "",
    text: "检测系统",
  });

  useEffect(() => {
    probe("/api/system/info").then((ok) => {
      if (ok) setSys({ cls: "green", text: "检测系统运行中" });
      else setSys({ cls: "red", text: "API 不可达" });
    });
  }, []);

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
        </div>
        <nav className="nav">
          {NAV_ITEMS.map((it) => (
            <NavLink
              key={it.key}
              to={it.to!}
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
