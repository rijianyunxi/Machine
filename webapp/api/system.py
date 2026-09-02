"""System info / stats / storage / logs API."""

import os
import signal
import threading

from fastapi import APIRouter, HTTPException, Query, Request

from webapp.api.common import get_state

router = APIRouter()


@router.get("/api/system/info")
def system_info(request: Request):
    return get_state(request).system_info()


@router.post("/api/system/restart")
def restart_service(request: Request):
    """Ask the process supervisor to restart the detection service.

    The normal ``main.py`` process installs a SIGTERM handler for graceful
    shutdown. In Docker, the compose ``restart: unless-stopped`` policy then
    starts it again. Delay the signal until this response has left the server
    so the browser receives a useful acknowledgement instead of a reset.
    """
    state = get_state(request)
    if state.system is None:
        raise HTTPException(400, "独立面板模式不支持重启服务，请重启启动命令")

    def _terminate() -> None:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except OSError:
            # The process may already be shutting down; there is nothing else
            # for this request to do.
            pass

    timer = threading.Timer(0.8, _terminate)
    timer.daemon = True
    timer.start()
    return {"ok": True, "message": "服务即将重启"}


@router.get("/api/system/stats")
def system_stats(request: Request):
    return get_state(request).stats()


@router.get("/api/system/stats/history")
def alert_trend(request: Request, days: int = Query(7, ge=1, le=90)):
    return {"trend": get_state(request).alert_trend(days=days)}


@router.get("/api/storage/usage")
def storage_usage(request: Request):
    return get_state(request).storage_usage()


@router.get("/api/logs")
def tail_logs(request: Request, tail: int = Query(500, ge=1, le=5000),
              level: str = Query(None)):
    return {"lines": get_state(request).tail_logs(tail=tail, level=level)}


@router.post("/api/retention/run")
def run_retention(request: Request):
    return get_state(request).run_retention()
