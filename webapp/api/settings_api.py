"""Settings API: schema-driven read + validated write with hot apply."""

from fastapi import APIRouter, HTTPException, Request

from webapp.api.common import get_state

router = APIRouter()


@router.get("/api/settings")
def all_settings(request: Request):
    state = get_state(request)
    return {"sections": state.get_settings(),
            "pending_restart": state.pending_restart()}


@router.put("/api/settings/{section}")
def update_settings(request: Request, section: str, values: dict):
    state = get_state(request)
    try:
        return state.update_settings(section, values)
    except ValueError as e:
        raise HTTPException(400, str(e))
