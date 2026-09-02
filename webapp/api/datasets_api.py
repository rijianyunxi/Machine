"""Dataset management + online annotation API."""

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from webapp.api.common import get_state

router = APIRouter()


@router.get("/api/datasets")
def datasets(request: Request):
    return {"datasets": get_state(request).datasets.list()}


@router.post("/api/datasets")
def create_dataset(request: Request, data: dict):
    try:
        return get_state(request).datasets.create(
            data.get("name", ""), data.get("classes", []))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/datasets/{name}")
def dataset_info(request: Request, name: str):
    try:
        return get_state(request).datasets.info(name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/api/datasets/{name}")
def delete_dataset(request: Request, name: str):
    try:
        get_state(request).datasets.delete(name)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.put("/api/datasets/{name}/classes")
def set_classes(request: Request, name: str, data: dict):
    try:
        get_state(request).datasets.set_classes(name, data.get("classes", []))
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/datasets/{name}/images")
async def upload_images(request: Request, name: str,
                        images: list[UploadFile] = File(...),
                        split: str = Form("train")):
    files = []
    for f in images:
        files.append((f.filename, await f.read()))
    try:
        added = get_state(request).datasets.add_images(name, files, split)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"added": added}


@router.post("/api/datasets/{name}/import_snapshots")
def import_snapshots(request: Request, name: str, data: dict):
    try:
        n = get_state(request).datasets.import_snapshots(
            name,
            date=data.get("date"),
            limit=int(data.get("limit", 300)),
            split=data.get("split", "train"),
        )
        return {"imported": n}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/datasets/{name}/images")
def list_images(request: Request, name: str, limit: int = 1000):
    try:
        return {"images": get_state(request).datasets.list_images(name, limit)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/datasets/{name}/images")
def delete_images(request: Request, name: str, data: dict):
    try:
        # New clients send split-aware image refs; keep old filenames payloads
        # working for callers that predate datasets with duplicate filenames.
        images = data.get("images", data.get("filenames", []))
        n = get_state(request).datasets.delete_images(name, images)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deleted": n}


@router.get("/api/datasets/{name}/image/{filename}")
def get_image(request: Request, name: str, filename: str,
             split: str = Query(None)):
    try:
        return FileResponse(
            get_state(request).datasets.image_path(name, filename, split=split))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/api/datasets/{name}/labels/{stem}")
def get_labels(request: Request, name: str, stem: str,
               split: str = Query(None)):
    try:
        return {"boxes": get_state(request).datasets.get_labels(
            name, stem, split=split)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/api/datasets/{name}/labels/{stem}")
def save_labels(request: Request, name: str, stem: str, data: dict,
                split: str = Query(None)):
    try:
        n = get_state(request).datasets.save_labels(
            name, stem, data.get("boxes", []), split=split)
        return {"saved": n}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/datasets/{name}/prelabel")
def start_prelabel(request: Request, name: str, data: dict):
    try:
        get_state(request).datasets.prelabel(
            name, model=data.get("model", ""),
            conf=float(data.get("conf", 0.4)),
            only_unlabeled=bool(data.get("only_unlabeled", True)),
            limit=int(data.get("limit", 200)))
        return {"ok": True}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.get("/api/datasets/{name}/prelabel_status")
def prelabel_status(request: Request, name: str):
    return get_state(request).datasets.prelabel_status()

