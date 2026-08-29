"""Camera CRUD + live preview API."""

import threading
import time

import cv2
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from webapp.api.common import abort_on_value_error, get_state

router = APIRouter()


@router.get("/api/cameras")
def cameras(request: Request):
    return {"cameras": get_state(request).cameras_status()}


@router.post("/api/cameras")
def add_camera(request: Request, data: dict):
    return abort_on_value_error(lambda: get_state(request).add_camera(data))


@router.put("/api/cameras/{camera_id}")
def update_camera(request: Request, camera_id: str, data: dict):
    return abort_on_value_error(
        lambda: get_state(request).update_camera(camera_id, data)
    )


@router.delete("/api/cameras/{camera_id}")
def delete_camera(request: Request, camera_id: str):
    abort_on_value_error(lambda: get_state(request).delete_camera(camera_id))
    return {"ok": True}


@router.post("/api/cameras/{camera_id}/restart")
def restart_camera(request: Request, camera_id: str):
    return abort_on_value_error(
        lambda: (get_state(request).restart_camera(camera_id), {"ok": True})[1]
    )


_THUMB_TTL = 1.0
_thumb_cache: dict = {}


def _jpeg_bytes(state, camera_id: str, quality=75, width=None):
    if state.system is None:
        raise HTTPException(400, "standalone 模式没有实时画面")
    # Downscaled thumbnails are the hot path on the dashboard (tens of cameras
    # polling at once): cache the encoded bytes briefly so concurrent panel
    # tabs / staggered refreshes share one encode per camera per interval.
    cacheable = bool(width)
    key = (camera_id, width)
    if cacheable:
        hit = _thumb_cache.get(key)
        if hit and time.monotonic() - hit[0] < _THUMB_TTL:
            return hit[1]
    manager = state.system._camera_manager
    frame_data = manager.get_frame(camera_id)
    if frame_data is None:
        raise HTTPException(404, "暂无画面（相机未连接或仍在重连）")
    frame = frame_data.frame
    if width and frame.shape[1] > width:
        h = max(int(frame.shape[0] * width / frame.shape[1]), 1)
        frame = cv2.resize(frame, (width, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame,
                           [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise HTTPException(500, "编码失败")
    data = buf.tobytes()
    if cacheable:
        if len(_thumb_cache) > 256:
            _thumb_cache.clear()
        _thumb_cache[key] = (time.monotonic(), data)
    return data


@router.get("/api/cameras/{camera_id}/frame.jpg")
def camera_frame(request: Request, camera_id: str,
                 w: int = Query(None, ge=80, le=1920)):
    return Response(_jpeg_bytes(get_state(request), camera_id, width=w),
                    media_type="image/jpeg")


@router.get("/api/cameras/{camera_id}/stream.mjpg")
def camera_stream(request: Request, camera_id: str):
    state = get_state(request)

    def gen():
        boundary = b"--frame\r\n"
        while True:
            try:
                frame = _jpeg_bytes(state, camera_id)
            except HTTPException:
                import time

                time.sleep(1.0)
                continue
            yield boundary + (
                b"Content-Type: image/jpeg\r\nContent-Length: "
                + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            )
            import time

            time.sleep(0.5)

    return StreamingResponse(gen(),
                             media_type="multipart/x-mixed-replace; boundary=frame")
