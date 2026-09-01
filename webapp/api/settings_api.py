"""Settings API: schema-driven read + validated write with hot apply."""

from fastapi import APIRouter, HTTPException, Request

from infrastructure.persistence import RevisionConflict
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
    payload = dict(values or {})
    expected_revision = payload.pop("expected_revision", None)
    if expected_revision is not None:
        try:
            expected_revision = int(expected_revision)
        except (TypeError, ValueError):
            raise HTTPException(400, "expected_revision 必须是整数")
    try:
        return state.update_settings(
            section, payload, expected_revision=expected_revision
        )
    except RevisionConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
