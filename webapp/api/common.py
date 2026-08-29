"""Shared helpers for API routers."""

from fastapi import HTTPException, Request


def get_state(request: Request):
    return request.app.state.state


def abort_on_value_error(fn):
    """Convert ValueError from the state layer into HTTP 400."""
    try:
        return fn()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
