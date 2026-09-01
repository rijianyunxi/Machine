"""Shared helpers for API routers."""

import sqlite3

from fastapi import HTTPException, Request

from infrastructure.persistence import RevisionConflict


def get_state(request: Request):
    return request.app.state.state


def abort_on_value_error(fn):
    """Convert ValueError from the state layer into HTTP 400."""
    try:
        return fn()
    except RevisionConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except sqlite3.OperationalError as e:
        message = str(e).lower()
        if "locked" in message or "busy" in message:
            raise HTTPException(
                status_code=503,
                detail="数据库正忙，请稍后重试；本次配置未提交",
                headers={"Retry-After": "1"},
            )
        raise
