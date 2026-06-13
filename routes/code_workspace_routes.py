"""Routes for sealed local code workspaces."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src import code_workspace
from src.auth_helpers import get_current_user


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(default="Workspace", max_length=80)


class FileWriteRequest(BaseModel):
    path: str = Field(max_length=500)
    content: str = Field(max_length=code_workspace.MAX_FILE_BYTES)


class PatchRequest(BaseModel):
    diff: str = Field(max_length=code_workspace.MAX_PATCH_BYTES)


class RunRequest(BaseModel):
    command: str = Field(max_length=400)
    timeout_seconds: int = Field(default=120, ge=1, le=code_workspace.MAX_COMMAND_SECONDS)


class CommitRequest(BaseModel):
    message: str = Field(default="Cleverly code workspace changes", max_length=140)


def _owner(request: Request) -> str:
    return get_current_user(request) or ""


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def setup_code_workspace_routes() -> APIRouter:
    router = APIRouter(
        prefix="/api/code-workspaces",
        tags=["code-workspaces"],
        dependencies=[Depends(require_admin)],
    )

    @router.get("")
    def list_code_workspaces(request: Request):
        return {"ok": True, "workspaces": code_workspace.list_workspaces(owner=_owner(request))}

    @router.post("")
    def create_code_workspace(body: CreateWorkspaceRequest, request: Request):
        try:
            return {"ok": True, "workspace": code_workspace.create_workspace(body.name, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/import")
    async def import_code_workspace(
        request: Request,
        name: str = Form(default=""),
        file: UploadFile = File(...),
    ):
        suffix = Path(file.filename or "repo.zip").suffix or ".zip"
        if (file.filename or "").lower().endswith(".tar.gz"):
            suffix = ".tar.gz"
        total = 0
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = Path(tmp.name)
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > code_workspace.MAX_ARCHIVE_BYTES:
                        raise code_workspace.CodeWorkspaceError("Archive is too large")
                    tmp.write(chunk)
            ws_name = name or Path(file.filename or "Workspace").stem
            return {
                "ok": True,
                "workspace": code_workspace.import_archive(ws_name, tmp_path, owner=_owner(request)),
            }
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)  # type: ignore[name-defined]
            except Exception:
                pass

    @router.delete("/{workspace_id}")
    def delete_code_workspace(workspace_id: str, request: Request):
        try:
            return {"ok": True, **code_workspace.delete_workspace(workspace_id, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.get("/{workspace_id}/tree")
    def code_workspace_tree(workspace_id: str, request: Request, path: str = ""):
        try:
            return {"ok": True, **code_workspace.list_tree(workspace_id, path, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.get("/{workspace_id}/file")
    def code_workspace_file(workspace_id: str, request: Request, path: str):
        try:
            return {"ok": True, **code_workspace.read_file(workspace_id, path, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.put("/{workspace_id}/file")
    def code_workspace_write(workspace_id: str, body: FileWriteRequest, request: Request):
        try:
            return {"ok": True, **code_workspace.write_file(workspace_id, body.path, body.content, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/patch")
    def code_workspace_patch(workspace_id: str, body: PatchRequest, request: Request):
        try:
            return {"ok": True, **code_workspace.apply_unified_diff(workspace_id, body.diff, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/run")
    def code_workspace_run(workspace_id: str, body: RunRequest, request: Request):
        try:
            return {
                "ok": True,
                **code_workspace.run_command(
                    workspace_id,
                    body.command,
                    owner=_owner(request),
                    timeout_seconds=body.timeout_seconds,
                ),
            }
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.get("/{workspace_id}/status")
    def code_workspace_status(workspace_id: str, request: Request):
        try:
            return {"ok": True, **code_workspace.git_status(workspace_id, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.get("/{workspace_id}/diff")
    def code_workspace_diff(workspace_id: str, request: Request, staged: bool = False):
        try:
            return {"ok": True, **code_workspace.git_diff(workspace_id, owner=_owner(request), staged=staged)}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/commit")
    def code_workspace_commit(workspace_id: str, body: CommitRequest, request: Request):
        try:
            return {"ok": True, **code_workspace.git_commit(workspace_id, body.message, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    return router
