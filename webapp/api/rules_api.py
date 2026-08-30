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


@router.get("/api/rules/template-logics")
def template_logics(request: Request):
    return {"logics": get_state(request).template_logics()}


@router.get("/api/rules/node-types")
def node_types(request: Request):
    """可视化规则画布的节点注册表（前端画布编辑器自动生成交互用）。"""
    return {"node_types": get_state(request).node_types()}


@router.post("/api/rules/templates")
def add_template(request: Request, data: dict):
    try:
        return get_state(request).create_template(data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/api/rules/templates/{name}")
def update_template(request: Request, name: str, data: dict):
    try:
        return get_state(request).update_template(name, data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/rules/templates/{name}")
def delete_template(request: Request, name: str):
    try:
        get_state(request).delete_template(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


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
