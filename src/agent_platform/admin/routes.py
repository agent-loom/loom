"""Admin UI 路由 — 提供管理仪表盘、Agent 管理、Eval 报告、DevFlow 看板、可观测性面板。

使用 Jinja2 模板 + Tailwind CSS CDN + Alpine.js 实现，无需前端构建步骤。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter(prefix="/admin", tags=["admin-ui"])


def _render(name: str, request: Request):
    return templates.TemplateResponse(request, name)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _render("dashboard.html", request)


@router.get("/agents", response_class=HTMLResponse)
async def agents_panel(request: Request):
    return _render("agents.html", request)


@router.get("/evals", response_class=HTMLResponse)
async def evals_panel(request: Request):
    return _render("evals.html", request)


@router.get("/devflow", response_class=HTMLResponse)
async def devflow_panel(request: Request):
    return _render("devflow.html", request)


@router.get("/observability", response_class=HTMLResponse)
async def observability_panel(request: Request):
    return _render("observability.html", request)


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_panel(request: Request):
    return _render("sessions.html", request)


@router.get("/deployments", response_class=HTMLResponse)
async def deployments_panel(request: Request):
    return _render("deployments.html", request)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render("login.html", request)
