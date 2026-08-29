"""Online rule configuration API (rules.yaml backed)."""

from fastapi import APIRouter, HTTPException, Request

from webapp.api.common import get_state

router = APIRouter()


@router.get("/api/rules")
def rules(request: Request):
    state = get_state(request)
    return {"rules": state.rules_list()}


@router.get("/api/rules/templates")
def templates(request: Request):
    return {"templates": get_state(request).template_specs()}


@router.post("/api/rules")
def add_rule(request: Request, data: dict):
    state = get_state(request)
    try:
        return state.add_rule(data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/api/rules/{rule_id}")
def update_rule(request: Request, rule_id: int, data: dict):
    state = get_state(request)
    try:
        return state.update_rule(rule_id, data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/rules/{rule_id}")
def delete_rule(request: Request, rule_id: int):
    state = get_state(request)
    try:
        state.delete_rule(rule_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
