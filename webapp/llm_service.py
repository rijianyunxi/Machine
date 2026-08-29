"""
LLM (OpenAI-compatible chat) client for AI-assisted labeling.

Config lives in settings.yaml `llm` section: enabled / base_url / api_key /
model. httpx is already a transitive dependency (ultralytics), so no new deps.
"""

import base64
import json
import re
from urllib.parse import urlparse

import httpx

from utils.logger import get_logger

logger = get_logger("panel.llm")


class LLMError(RuntimeError):
    pass


def get_config(state) -> dict:
    cfg = state.settings().get("llm", {}) or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "base_url": str(cfg.get("base_url", "https://api.openai.com/v1")).rstrip("/"),
        "api_key": str(cfg.get("api_key", "")),
        "model": str(cfg.get("model", "gpt-4o-mini")),
    }


def list_models(state) -> list:
    """GET {base_url}/models (OpenAI-compatible). Requires saved config."""
    cfg = get_config(state)
    if not cfg["api_key"]:
        raise LLMError("未配置 API Key，请先填写并保存")
    local = urlparse(cfg["base_url"]).hostname in ("127.0.0.1", "localhost", "::1")
    try:
        resp = httpx.get(
            f"{cfg['base_url']}/models",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            timeout=20.0,
            trust_env=not local,
        )
    except httpx.HTTPError as e:
        raise LLMError(f"获取模型列表失败: {e}")
    if resp.status_code != 200:
        raise LLMError(f"获取模型列表失败 {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json().get("data", [])
        return sorted(m.get("id") for m in data if m.get("id"))
    except (ValueError, AttributeError):
        raise LLMError(f"模型列表格式异常: {resp.text[:200]}")


def chat(state, prompt: str, image_bytes: bytes = None,
         image_mime: str = "image/jpeg") -> str:
    cfg = get_config(state)
    if not cfg["enabled"]:
        raise LLMError("LLM 未启用（系统设置 → 大模型）")
    if not cfg["api_key"]:
        raise LLMError("未配置 API Key（系统设置 → 大模型）")

    content = [{"type": "text", "text": prompt}]
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{image_mime};base64,{b64}"}})

    # Local endpoints (vLLM / Ollama etc.) must bypass env proxies, otherwise
    # a system-wide proxy silently hijacks 127.0.0.1 calls and returns 5xx.
    local = urlparse(cfg["base_url"]).hostname in ("127.0.0.1", "localhost", "::1")
    try:
        resp = httpx.post(
            f"{cfg['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={"model": cfg["model"],
                  "messages": [{"role": "user", "content": content}],
                  "temperature": 0.2},
            timeout=60.0,
            trust_env=not local,
        )
    except httpx.HTTPError as e:
        raise LLMError(f"LLM 请求失败: {e}")

    if resp.status_code != 200:
        raise LLMError(f"LLM 返回 {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise LLMError(f"LLM 响应格式异常: {resp.text[:200]}")


def _match_class(label: str, classes: list):
    """Exact then substring match of an LLM label against dataset classes."""
    l = label.lower().strip()
    if not l:
        return None
    for i, c in enumerate(classes):
        if l == str(c).lower().strip():
            return i
    for i, c in enumerate(classes):
        c = str(c).lower().strip()
        if c and (l in c or c in l):
            return i
    return None


def parse_boxes(text: str, classes: list) -> list:
    """Parse LLM JSON boxes; labels outside the allowed classes are dropped."""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for b in raw:
        try:
            label = str(b.get("label", ""))
            x, y = float(b["x"]), float(b["y"])
            w, h = float(b["w"]), float(b["h"])
        except (KeyError, TypeError, ValueError):
            continue
        cls = _match_class(label, classes or [])
        if cls is None:
            continue  # label not in the dataset's class list -> ignore
        if x > 1 or y > 1 or w > 1 or h > 1:  # percentages given as 0-100
            x, y, w, h = x / 100, y / 100, w / 100, h / 100
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
            continue
        out.append({"cls": cls, "x": x, "y": y, "w": w, "h": h})
    return out
