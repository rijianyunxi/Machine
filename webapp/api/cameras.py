"""Camera CRUD + live preview + stream test API."""

import os
import threading
import time

import cv2
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from webapp.api.common import abort_on_value_error, get_state

router = APIRouter()

_test_lock = threading.Lock()  # one stream test at a time


@router.post("/api/cameras/test")
def test_camera_stream(request: Request, data: dict):
    """Try opening an RTSP/local URL and grabbing one frame.

    Accepts the `__KEEP__` password placeholder (resolved against the stored
    config) so the test works without the panel ever seeing the password.
    """
    state = get_state(request)
    url = str(data.get("url", "")).strip()
    if not url:
        raise HTTPException(400, "缺少地址")

    if "__KEEP__@" in url:
        cam_id = data.get("camera_id", "")
        old = next((c.get("rtsp_url", "") for c in state.config.get_cameras()
                    if c.get("id") == cam_id), "")
        merged = state._merge_keep_password(url, old)
        if "__KEEP__@" in merged:
            raise HTTPException(400, "无法解析原密码，请直接填写密码")
        url = merged

    with _test_lock:  # also protects the temporary env mutation below
        old_env = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        # 测试统一走 TCP（与部署建议一致）
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        try:
            cap = cv2.VideoCapture()
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
            opened = cap.open(url, cv2.CAP_FFMPEG)
            if not opened:
                return {"ok": False,
                        "error": "无法建立连接：地址/端口/认证错误，或网络不可达"}
            t0 = time.time()
            ok, frame = cap.read()
            latency = int((time.time() - t0) * 1000)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = round(cap.get(cv2.CAP_PROP_FPS) or 0, 1)
            cap.release()
            if not ok:
                return {"ok": False, "error": "连接成功但取帧失败（流不稳定或编码不支持）"}
            return {"ok": True, "width": w, "height": h, "fps": fps,
                    "latency_ms": latency}
        finally:
            if old_env is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old_env


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
