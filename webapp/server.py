"""
Panel web server.

Embedded mode : PanelServer(system).start() from main.py —
                runs uvicorn in a daemon thread sharing the live system.
Standalone    : python -m webapp.server — read-only panel
                (history browsing + detection test bench) without the main
                detection process.
"""

import argparse
import base64
import hmac
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from utils.logger import get_logger
from utils.passwords import session_token, verify_password

from webapp.state import RuntimeState

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logger = get_logger("panel.server")


# ----------------------------------------------------------------------
# App factory
# ----------------------------------------------------------------------

def create_app(state: RuntimeState) -> FastAPI:
    from webapp.api import alerts, cameras, datasets_api, detect_api, \
        llm_api, models_api, rules_api, settings_api, system, train_api

    app = FastAPI(title="Machine · 机器视觉安全行为检测面板", docs_url="/api/docs",
                  redoc_url=None)

    app.state.state = state

    for router in (system.router, cameras.router, alerts.router,
                   models_api.router, rules_api.router, settings_api.router,
                   detect_api.router, datasets_api.router, train_api.router,
                   llm_api.router):
        app.include_router(router)

    @app.middleware("http")
    async def prevent_stale_spa_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/app/assets/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    # snapshot files (root-confined to save_dir)
    snapshots_dir = state.snapshots_dir()
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/snapshots", StaticFiles(directory=str(snapshots_dir)),
              name="snapshots")

    # test-result annotated images also need to be web-accessible
    test_dir = PROJECT_ROOT / "storage" / "test_results"
    test_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/test_results", StaticFiles(directory=str(test_dir)),
              name="test_results")

    # ---------------- React SPA (/app) ----------------
    # The SPA is the only UI. Shell is exempt from auth (client-side gate +
    # 401 handling); all data APIs stay protected.

    SPA_DIST = Path(__file__).resolve().parent / "spa" / "dist"
    if (SPA_DIST / "index.html").exists():
        from fastapi.responses import FileResponse

        if (SPA_DIST / "assets").exists():
            app.mount("/app/assets",
                      StaticFiles(directory=str(SPA_DIST / "assets")),
                      name="spa_assets")

        async def spa_index():
            # hashed assets are immutable; the shell itself must revalidate
            return FileResponse(SPA_DIST / "index.html",
                                headers={"Cache-Control": "no-cache"})

        app.get("/app", include_in_schema=False)(spa_index)
        app.get("/app/{path:path}", include_in_schema=False)(spa_index)

    @app.get("/")
    async def index():
        return RedirectResponse("/app")

    # 旧 UI 已下线：旧页面路径一律 307 到新 UI 对应页（书签兼容）。
    def legacy_redirect(target: str):
        async def view(request: Request):
            return RedirectResponse(target)
        return view

    for name in ("dashboard", "cameras", "models", "datasets", "annotate",
                 "train", "rules", "detect", "alerts", "snapshots",
                 "settings", "logs", "login"):
        app.get(f"/{name}", name=f"legacy_{name}", include_in_schema=False)(
            legacy_redirect(f"/app/{name}"))

    # ---------------- auth ----------------

    panel_cfg = state.settings().get("panel", {})
    auth_enabled = bool(panel_cfg.get("auth_enabled", True))
    username = str(panel_cfg.get("username", "admin"))
    password_hash = str(panel_cfg.get("password", ""))
    cookie_token = session_token(username, password_hash)
    COOKIE = "panel_token"

    from fastapi.responses import JSONResponse

    def _check_basic_header(header: str) -> bool:
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
            supplied_user, supplied_password = raw.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return (hmac.compare_digest(supplied_user, username)
                and verify_password(supplied_password, password_hash))

    @app.post("/api/login")
    async def login(data: dict):
        supplied_user = str(data.get("username", ""))
        supplied_password = str(data.get("password", ""))
        if (hmac.compare_digest(supplied_user, username)
                and verify_password(supplied_password, password_hash)):
            resp = JSONResponse({"ok": True})
            resp.set_cookie(COOKIE, cookie_token, max_age=7 * 86400,
                            httponly=True, samesite="lax")
            return resp
        return JSONResponse(status_code=401, content={"detail": "用户名或密码错误"})

    # Paths that work without auth: login endpoint + static assets + SPA shell
    # (all data APIs stay protected).
    EXEMPT_PREFIXES = ("/api/login", "/static/", "/favicon", "/app")
    # "/" and /login must match EXACTLY — a prefix match on "/" would exempt
    # every path and silently disable auth entirely.
    EXEMPT_EXACT = ("/login", "/")

    class PanelAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if not auth_enabled:
                return await call_next(request)
            path = request.url.path
            if path in EXEMPT_EXACT or path.startswith(EXEMPT_PREFIXES):
                return await call_next(request)
            header = request.headers.get("Authorization", "")
            cookie = request.cookies.get(COOKIE, "")
            if _check_basic_header(header) or hmac.compare_digest(cookie, cookie_token):
                return await call_next(request)
            if path.startswith("/api/"):
                return JSONResponse(status_code=401,
                                    content={"detail": "需要登录"})
            return RedirectResponse("/login")

    app.add_middleware(PanelAuth)

    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------

class PanelServer:
    """Runs the FastAPI app on a uvicorn server inside a daemon thread."""

    def __init__(self, system=None):
        self.state = RuntimeState(system=system)
        panel_cfg = self.state.settings().get("panel", {})
        self.host = panel_cfg.get("host", "0.0.0.0")
        self.port = int(panel_cfg.get("port", 8000))
        self.enabled = bool(panel_cfg.get("enabled", True))
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if not self.enabled:
            logger.info("Panel disabled by settings (panel.enabled=false)")
            return
        app = create_app(self.state)
        config = uvicorn.Config(app, host=self.host, port=self.port,
                                log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run,
                                        name="panel-server", daemon=True)
        self._thread.start()
        logger.info(f"Panel starting on http://{self.host}:{self.port}")

    def wait_ready(self, timeout: float = 10.0) -> bool:
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._server is not None and self._server.started:
                return True
            time.sleep(0.1)
        return False

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        logger.info("Panel stopped")


def main():
    parser = argparse.ArgumentParser(description="独立模式启动面板（只读+测试台）")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    server = PanelServer(system=None)
    if args.host:
        server.host = args.host
    if args.port:
        server.port = args.port
    server.start()
    if not server.wait_ready():
        print("[FATAL] 面板启动失败（端口被占用？）", flush=True)
        raise SystemExit(1)
    print(f"[OK] 面板已启动: http://{server.host}:{server.port}", flush=True)
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
