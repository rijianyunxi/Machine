"""Online training API."""

from fastapi import APIRouter, HTTPException, Request

from webapp.api.common import get_state

router = APIRouter()


@router.get("/api/train/status")
def train_status(request: Request):
    return get_state(request).trainer.status()


@router.post("/api/train/start")
def train_start(request: Request, data: dict):
    try:
        return get_state(request).trainer.start(
            dataset=data.get("dataset", ""),
            base_model=data.get("base_model", ""),
            epochs=int(data.get("epochs", 100)),
            imgsz=int(data.get("imgsz", 640)),
            batch=int(data.get("batch", 16)),
            device=data.get("device", "auto"),
            name=data.get("name", ""),
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/api/train/stop")
def train_stop(request: Request):
    stopped = get_state(request).trainer.stop()
    return {"stopped": stopped}


@router.get("/api/train/runs")
def train_runs(request: Request):
    return {"runs": get_state(request).trainer.runs()}


@router.post("/api/train/register")
def train_register(request: Request, data: dict):
    try:
        return get_state(request).trainer.register_best(
            data.get("run", ""), data.get("model_name", ""))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))
