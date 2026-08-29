"""System info / stats / storage / logs API."""

from fastapi import APIRouter, Query, Request

from webapp.api.common import get_state

router = APIRouter()


@router.get("/api/system/info")
def system_info(request: Request):
    return get_state(request).system_info()


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
