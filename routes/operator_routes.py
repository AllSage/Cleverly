"""Operator readiness and air-gap checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.middleware import require_admin
from src.operator_checks import run_operator_checks


def setup_operator_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/operator",
        tags=["operator"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("/checks")
    def operator_checks():
        return {"ok": True, **run_operator_checks()}

    return router
