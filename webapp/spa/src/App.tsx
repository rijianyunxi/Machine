import { useEffect, useRef, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { probe, setLoginPrompt } from "./api/client";
import { AppLayout } from "./layout/AppLayout";
import { ConfirmProvider } from "./ui/Confirm";
import { LightboxProvider } from "./ui/Lightbox";
import { ToastProvider } from "./ui/Toast";
import { LoginForm } from "./pages/Login";
import { Modal } from "./ui/Modal";
import DashboardPage from "./pages/Dashboard";
import CamerasPage from "./pages/Cameras";
import ModelsPage from "./pages/Models";
import DatasetsPage from "./pages/Datasets";
import AnnotatePage from "./pages/Annotate";
import RulesPage from "./pages/Rules";
import DetectPage from "./pages/Detect";
import AlertsPage from "./pages/Alerts";
import SnapshotsPage from "./pages/Snapshots";
import SettingsPage from "./pages/Settings";
import LogsPage from "./pages/Logs";
import TrainPage from "./pages/Train";

function InnerApp() {
  const location = useLocation();
  const onLoginPage = location.pathname === "/login";
  // null = 会话检查中；false = 未登录；true = 已登录
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);
  const resolver = useRef<((v: boolean) => void) | null>(null);

  useEffect(() => {
    probe("/api/system/info").then(setAuthed);
    // API 客户端遇到 401 时弹登录框，成功后重试原请求。
    // 登录页本身已经展示登录表单，不再叠加一次登录弹窗。
    setLoginPrompt(
      () => {
        if (window.location.pathname.endsWith("/login"))
          return Promise.resolve(false);
        return new Promise<boolean>((resolve) => {
          resolver.current = resolve;
          setLoginOpen(true);
        });
      },
    );
  }, []);

  const finish = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setLoginOpen(false);
    if (ok) setAuthed(true);
  };

  // 从登录弹窗切换到独立登录页时，结束原请求，避免弹窗残留并遮住登录表单。
  useEffect(() => {
    if (onLoginPage && loginOpen) finish(false);
  }, [onLoginPage, loginOpen]);

  return (
    <>
      <Routes>
        <Route
          path="/login"
          element={<LoginForm onOk={() => setAuthed(true)} />}
        />
        <Route
          element={
            authed === false ? (
              <Navigate to="/login" replace />
            ) : authed === true ? (
              <AppLayout />
            ) : null
          }
        >
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/cameras" element={<CamerasPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/annotate" element={<AnnotatePage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/detect" element={<DetectPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/snapshots" element={<SnapshotsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/train" element={<TrainPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
      {loginOpen && !onLoginPage && (
        <Modal title="登录面板" width={380} onClose={() => finish(false)}>
          <LoginForm
            compact
            onOk={() => finish(true)}
            onCancel={() => finish(false)}
          />
        </Modal>
      )}
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <ToastProvider>
        <ConfirmProvider>
          <LightboxProvider>
            <InnerApp />
          </LightboxProvider>
        </ConfirmProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
