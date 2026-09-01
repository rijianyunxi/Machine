"""Model file management (upload/validate/register) + registry control API."""

from fastapi import APIRouter, HTTPException, Request, UploadFile

from infrastructure.persistence import RevisionConflict
from webapp.api.common import abort_on_value_error, get_state

router = APIRouter()


@router.get("/api/models")
def models(request: Request):
    state = get_state(request)
    return {"models": state.models_status(), "files": state.model_files(),
            "load_jobs": getattr(state, "_load_jobs", {})}


@router.post("/api/models/files")
async def upload_model(request: Request, file: UploadFile):
    state = get_state(request)
    content = await file.read()
    try:
        saved = state.upload_model(file.filename, content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "file": saved, "validation": "校验中"}


@router.post("/api/models/files/{filename}/validate")
def validate_model(request: Request, filename: str):
    state = get_state(request)
    try:
        state.validate_model(filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/api/models/files/{filename}")
def delete_model_file(request: Request, filename: str):
    state = get_state(request)
    try:
        state.delete_model_file(filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/models")
def register_model(request: Request, data: dict):
    state = get_state(request)
    return abort_on_value_error(lambda: state.register_model(
        name=data.get("name", ""),
        file=data.get("file", ""),
        enabled=bool(data.get("enabled", False)),
        confidence_override=data.get("confidence_override"),
        expected_revision=data.get("expected_revision"),
    ))


@router.put("/api/models/{name}")
def update_model(request: Request, name: str, data: dict):
    return abort_on_value_error(
        lambda: get_state(request).update_model(name, data)
    )


@router.post("/api/models/{name}/reload")
def reload_model(request: Request, name: str):
    state = get_state(request)
    try:
        state.reload_model(name)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.delete("/api/models/{name}")
def unregister_model(request: Request, name: str, expected_revision: int | None = None):
    state = get_state(request)
    try:
        state.unregister_model(name, expected_revision=expected_revision)
    except RevisionConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
