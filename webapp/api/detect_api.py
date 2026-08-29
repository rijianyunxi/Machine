"""Detection test bench API: upload image / grab camera frame -> detections."""

import numpy as np
import cv2
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from webapp.api.common import get_state

router = APIRouter()

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _decode_image(content: bytes, filename: str) -> np.ndarray:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        raise HTTPException(400, "仅支持 jpg/jpeg/png/webp 图片")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "图片超过 20MB 上限")
    arr = np.frombuffer(content, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "图片解码失败")
    return img


@router.post("/api/detect/test")
async def detect_test(
    request: Request,
    image: UploadFile = File(None),
    models: str = Form(""),
    conf: float = Form(None),
    iou: float = Form(None),
):
    state = get_state(request)
    if image is None:
        raise HTTPException(400, "缺少图片文件")
    content = await image.read()
    img = _decode_image(content, image.filename or "")
    model_names = [m.strip() for m in models.split(",") if m.strip()] or None
    try:
        return state.run_detection_test(img, model_names=model_names,
                                        conf=conf, iou=iou)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/api/detect/test/camera/{camera_id}")
def detect_test_camera(request: Request, camera_id: str,
                       models: str = "", conf: float = None):
    state = get_state(request)
    if state.system is None:
        raise HTTPException(400, "standalone 模式没有相机画面")
    frame_data = state.system._camera_manager.get_frame(camera_id)
    if frame_data is None:
        raise HTTPException(404, "该相机暂无画面")
    model_names = [m.strip() for m in models.split(",") if m.strip()] or None
    try:
        return state.run_detection_test(frame_data.frame, model_names=model_names,
                                        conf=conf)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.get("/api/detect/test/history")
def detect_history(request: Request):
    return {"results": get_state(request).recent_test_results()}


@router.get("/api/detect/test/{result_id}/annotated.jpg")
def detect_annotated(request: Request, result_id: int):
    try:
        return Response(get_state(request)._test_service().annotated_jpeg(result_id),
                        media_type="image/jpeg")
    except FileNotFoundError:
        raise HTTPException(404, "结果不存在或已清理")
