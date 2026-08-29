"""LLM chat API (OpenAI-compatible) for AI-assisted labeling."""

import base64

from fastapi import APIRouter, HTTPException, Request

from webapp.api.common import get_state
from webapp.llm_service import LLMError, chat, get_config, list_models, parse_boxes

router = APIRouter()


@router.get("/api/llm/config")
def llm_config(request: Request):
    cfg = get_config(get_state(request))
    cfg["api_key"] = "****" if cfg["api_key"] else ""
    return cfg


@router.post("/api/llm/test")
def llm_test(request: Request):
    try:
        reply = chat(get_state(request), "回复两个字：正常")
        return {"ok": True, "reply": reply.strip()[:100]}
    except LLMError as e:
        raise HTTPException(400, str(e))


@router.post("/api/llm/models")
def llm_models(request: Request):
    """Fetch available model ids from the configured (saved) endpoint."""
    try:
        return {"models": list_models(get_state(request))}
    except LLMError as e:
        raise HTTPException(400, str(e))


@router.post("/api/llm/chat")
def llm_chat(request: Request, data: dict):
    image_b64 = data.get("image", "")  # data URL: data:image/jpeg;base64,...
    image_bytes = None
    if image_b64:
        try:
            image_bytes = base64.b64decode(image_b64.split(",", 1)[-1])
        except Exception:
            raise HTTPException(400, "图片 base64 解码失败")
    try:
        text = chat(get_state(request), data.get("prompt", ""),
                    image_bytes=image_bytes)
        classes = data.get("classes", [])
        boxes = parse_boxes(text, classes) if image_bytes and classes else []
        return {"text": text, "boxes": boxes}
    except LLMError as e:
        raise HTTPException(400, str(e))
