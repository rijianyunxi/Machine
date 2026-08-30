"""Alerts (with false-positive marking) + snapshot gallery API."""

import re
import time
from datetime import datetime

import cv2
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from webapp.api.common import abort_on_value_error, get_state

router = APIRouter()

ALERT_STATUSES = ("new", "confirmed", "false_positive", "resolved")


@router.get("/api/alerts")
def list_alerts(
    request: Request,
    camera: str = Query(None),
    rule: int = Query(None),
    status: str = Query(None),
    days: int = Query(None, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    state = get_state(request)
    from_ts = to_ts = None
    if days:
        to_ts = time.time()
        from_ts = to_ts - days * 86400
    if status and status not in ALERT_STATUSES:
        raise HTTPException(400, f"status 需为 {ALERT_STATUSES}")
    return abort_on_value_error(
        lambda: state.db.get_alerts(camera=camera, rule_id=rule, status=status,
                                    from_ts=from_ts, to_ts=to_ts,
                                    limit=limit, offset=offset)
    )


@router.get("/api/alerts/summary")
def alert_summary(request: Request, days: int = Query(7, ge=1, le=90)):
    return get_state(request).db.get_alert_summary(days=days)


@router.post("/api/alerts/{alert_id}/status")
def set_alert_status(request: Request, alert_id: int, data: dict):
    state = get_state(request)
    status = data.get("status")
    if status not in ALERT_STATUSES:
        raise HTTPException(400, f"status 需为 {ALERT_STATUSES}")
    ok = state.db.update_alert_status(alert_id, status, note=data.get("note"))
    if not ok:
        raise HTTPException(404, "告警不存在")
    return {"ok": True, "status": status}


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/api/snapshots")
def snapshots(request: Request, date: str = Query(None),
              from_date: str = Query(None), to_date: str = Query(None),
              rule: str = Query(None), camera: str = Query(None),
              limit: int = Query(200, ge=1, le=1000),
              offset: int = Query(0, ge=0)):
    for name, value in (("date", date), ("from_date", from_date),
                        ("to_date", to_date)):
        if value and not _DATE_RE.match(value):
            raise HTTPException(400, f"{name} 格式需为 YYYY-MM-DD")
    if from_date and to_date and from_date > to_date:
        raise HTTPException(400, "from_date 不能晚于 to_date")
    return get_state(request).list_snapshots(date=date, from_date=from_date,
                                             to_date=to_date, rule=rule,
                                             camera=camera, limit=limit,
                                             offset=offset)


@router.get("/api/snapshots/thumb")
def snapshot_thumb(request: Request, p: str = Query(...),
                   w: int = Query(420, ge=120, le=960)):
    """Downscaled snapshot copy with an on-disk cache (.thumbs/wNNN/...).
    The grid loads these instead of full-resolution originals."""
    state = get_state(request)
    base = state.snapshots_dir().resolve()
    src = (base / p).resolve()
    if not str(src).startswith(str(base)) or src.suffix.lower() != ".jpg" \
            or not src.is_file():
        raise HTTPException(404, "快照不存在")
    cache = base / ".thumbs" / f"w{w}" / p
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file():
        img = cv2.imread(str(src))
        if img is None:
            raise HTTPException(500, "快照读取失败")
        if img.shape[1] > w:
            h = max(int(img.shape[0] * w / img.shape[1]), 1)
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            raise HTTPException(500, "缩略图生成失败")
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(buf.tobytes())
        tmp.replace(cache)   # atomic-ish so concurrent requests never see partial
    return FileResponse(cache, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.post("/api/snapshots/cleanup")
def cleanup_snapshots(request: Request, data: dict):
    state = get_state(request)
    before = data.get("before_date")
    if not before:
        raise HTTPException(400, "缺少 before_date（YYYY-MM-DD）")
    try:
        datetime.strptime(before, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "before_date 格式需为 YYYY-MM-DD")
    return {"deleted_dirs": state.cleanup_snapshots(before)}
