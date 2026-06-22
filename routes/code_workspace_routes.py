"""Routes for sealed local code workspaces."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src import code_workspace
from src import code_workspace_agent
from src.auth_helpers import get_current_user
from src.local_audit import append_audit
from src.operator_activity import upsert_operator_activity_record


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(default="Workspace", max_length=80)


class FileWriteRequest(BaseModel):
    path: str = Field(max_length=500)
    content: str = Field(max_length=code_workspace.MAX_FILE_BYTES)
    allowed_paths: list[str] = Field(default_factory=list, max_length=code_workspace.MAX_ALLOWED_PREFIXES)


class PatchRequest(BaseModel):
    diff: str = Field(max_length=code_workspace.MAX_PATCH_BYTES)
    allowed_paths: list[str] = Field(default_factory=list, max_length=code_workspace.MAX_ALLOWED_PREFIXES)


class RunRequest(BaseModel):
    command: str = Field(max_length=400)
    timeout_seconds: int = Field(default=120, ge=1, le=code_workspace.MAX_COMMAND_SECONDS)


class CommitRequest(BaseModel):
    message: str = Field(default="Cleverly code workspace changes", max_length=140)


class SnapshotRequest(BaseModel):
    label: str = Field(default="Manual snapshot", max_length=120)


class AgentRequest(BaseModel):
    task: str = Field(min_length=1, max_length=6000)
    model_key: str = Field(default="", max_length=200)
    test_command: str = Field(default="", max_length=400)
    max_rounds: int = Field(default=2, ge=1, le=3)
    selected_paths: list[str] = Field(default_factory=list, max_length=12)
    allowed_paths: list[str] = Field(default_factory=list, max_length=code_workspace.MAX_ALLOWED_PREFIXES)
    apply_changes: bool = False


class ValidateDiffRequest(BaseModel):
    diff: str = Field(max_length=code_workspace.MAX_PATCH_BYTES)
    test_command: str = Field(min_length=1, max_length=400)
    allowed_paths: list[str] = Field(default_factory=list, max_length=code_workspace.MAX_ALLOWED_PREFIXES)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _owner(request: Request) -> str:
    return get_current_user(request) or ""


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _activity_text(value: object, limit: int = 2200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 28)].strip()}\n[truncated for activity]"


def _workspace_name(workspace_id: str, owner: str) -> str:
    try:
        return str(code_workspace.get_workspace(workspace_id, owner=owner).get("name") or workspace_id)
    except Exception:
        return workspace_id


def _record_run_activity(workspace_id: str, command: str, result: dict, owner: str, *, blocked: bool = False) -> dict:
    exit_code = result.get("exit_code")
    ok = exit_code == 0
    runner = str(result.get("runner") or "workspace runner")
    workspace = _workspace_name(workspace_id, owner)
    stdout = _activity_text(result.get("stdout"))
    stderr = _activity_text(result.get("stderr"))
    detail = f"Ran \"{command.strip()}\" in {workspace}; exit_code={exit_code}; runner={runner}."
    if blocked:
        detail = f"Blocked \"{command.strip() or 'workspace command'}\" in {workspace}; {stderr or 'request rejected'}."
    output_parts = [
        f"stdout:\n{stdout}" if stdout else "",
        f"stderr:\n{stderr}" if stderr else "",
    ]
    title = "Code Workspace Command Blocked" if blocked else ("Code Workspace Command Passed" if ok else "Code Workspace Command Failed")
    status = "blocked" if blocked else ("success" if ok else "error")
    state = "error" if blocked else ("ok" if ok else "error")
    policy = (
        "Command execution was blocked by Code Workspace validation before shell work started"
        if blocked
        else "Command execution happened through the Code Workspace run endpoint"
    )
    record = {
        "command_id": "run-tests",
        "title": title,
        "category": "Code",
        "status": status,
        "state": state,
        "source": "code-workspace-api",
        "trust": "approval",
        "trust_mode": "ask",
        "detail": detail,
        "workspace_id": workspace_id,
        "workspace": workspace,
        "run_command": command.strip(),
        "exit_code": exit_code,
        "runner": runner,
        "stdout": stdout,
        "stderr": stderr,
        "preview": {
            "title": title,
            "intent": command.strip() or "workspace command",
            "source": "code-workspace-api",
            "category": "Code",
            "trust": "approval",
            "trust_label": "Approval",
            "trust_mode": "ask",
            "scope": "Sealed Code Workspace runner",
            "policy": policy,
            "safety_note": "Use Code Workspace status, diff, snapshots, and activity retry/recovery before further changes.",
            "flags": [
                {"label": "Exit Code", "value": str(exit_code), "state": "ok" if ok else "error"},
                {"label": "Runner", "value": runner, "state": "warn" if blocked else "ok"},
                {"label": "Workspace", "value": workspace, "state": "ok"},
                {"label": "Recovery", "value": "Review command, diff, or snapshot before retry", "state": "ok" if ok else "warn"},
            ],
        },
        "events": [{
            "at": _utc_now(),
            "status": status,
            "state": state,
            "detail": "\n".join([detail, *[part for part in output_parts if part]]),
        }],
    }
    return upsert_operator_activity_record(record, owner=owner)


def _record_agent_activity(
    workspace_id: str,
    body: AgentRequest,
    result: dict | None,
    owner: str,
    *,
    error: Exception | None = None,
) -> dict:
    workspace = _workspace_name(workspace_id, owner)
    failed = error is not None or (result or {}).get("exit_code") not in (0, None)
    blocked = error is not None
    status = "blocked" if blocked else ("error" if failed else "success")
    state = "error" if failed else "ok"
    selected_paths = list((result or {}).get("selected_paths") or body.selected_paths or [])[:12]
    steps = [step for step in (result or {}).get("steps", []) if isinstance(step, dict)]
    test_result = (result or {}).get("test_result") if isinstance((result or {}).get("test_result"), dict) else None
    model = (result or {}).get("model") or (result or {}).get("model_key") or body.model_key or ""
    has_diff = bool((result or {}).get("proposed_diff") or (result or {}).get("applied_diff"))
    applied = bool((result or {}).get("applied"))
    snapshot = (result or {}).get("snapshot") if isinstance((result or {}).get("snapshot"), dict) else {}
    exit_code = (result or {}).get("exit_code")
    if blocked:
        exit_code = 1
    stderr = _activity_text(str(error or "") or (test_result or {}).get("stderr") or "")
    stdout = _activity_text((test_result or {}).get("stdout") or "")
    detail = (
        f"Agent {'blocked' if blocked else 'completed'} \"{body.task.strip()}\" in {workspace}; "
        f"exit_code={exit_code}; model={model or 'unresolved'}; files={len(selected_paths)}; "
        f"diff={'yes' if has_diff else 'no'}; applied={'yes' if applied else 'no'}."
    )
    flags = [
        {"label": "Exit Code", "value": str(exit_code), "state": "ok" if not failed else "error"},
        {"label": "Model", "value": str(model or "unresolved"), "state": "ok" if model else "warn"},
        {"label": "Files", "value": str(len(selected_paths)), "state": "ok" if selected_paths else "warn"},
        {"label": "Diff", "value": "Applied" if applied else ("Drafted" if has_diff else "None"), "state": "ok" if has_diff else "warn"},
        {"label": "Recovery", "value": "Snapshot available" if snapshot.get("id") else "Review workspace state", "state": "ok" if snapshot.get("id") else "warn"},
    ]
    if test_result:
        flags.append({"label": "Tests", "value": str(test_result.get("exit_code")), "state": "ok" if test_result.get("exit_code") == 0 else "error"})
    record = {
        "command_id": "open-code-preflight",
        "title": "Code Workspace Agent Blocked" if blocked else ("Code Workspace Agent Failed" if failed else "Code Workspace Agent Completed"),
        "category": "Code",
        "status": status,
        "state": state,
        "source": "code-workspace-agent-api",
        "trust": "approval",
        "trust_mode": "ask",
        "detail": detail,
        "workspace_id": workspace_id,
        "workspace": workspace,
        "agent_task": body.task.strip(),
        "model": str(model or ""),
        "selected_paths": selected_paths,
        "snapshot_id": snapshot.get("id", ""),
        "has_proposed_diff": bool((result or {}).get("proposed_diff")),
        "applied": applied,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "preview": {
            "title": "Code Workspace Agent",
            "intent": body.task.strip() or "coding agent task",
            "source": "code-workspace-agent-api",
            "category": "Code",
            "trust": "approval",
            "trust_label": "Approval",
            "trust_mode": "ask",
            "scope": "Sealed Code Workspace agent draft",
            "policy": "Agent output stays in Code Workspace review until the user validates and applies it",
            "safety_note": "Review proposed diff, tests, status, and snapshot before applying or retrying.",
            "flags": flags,
        },
        "events": [{
            "at": _utc_now(),
            "status": status,
            "state": state,
            "detail": detail,
        }],
        "steps": [
            {
                "phase": str(step.get("phase") or ""),
                "round": step.get("round"),
                "exit_code": step.get("exit_code"),
                "error": _activity_text(step.get("error") or step.get("stderr") or "", 500),
            }
            for step in steps[:12]
        ],
    }
    return upsert_operator_activity_record(record, owner=owner)


def _record_validation_activity(
    workspace_id: str,
    body: ValidateDiffRequest,
    owner: str,
    *,
    snapshot: dict | None = None,
    patch: dict | None = None,
    test: dict | None = None,
    error: Exception | None = None,
) -> dict:
    workspace = _workspace_name(workspace_id, owner)
    patch_exit = (patch or {}).get("exit_code")
    test_exit = (test or {}).get("exit_code")
    blocked = error is not None
    valid = bool(not blocked and patch_exit == 0 and (test_exit == 0))
    status = "blocked" if blocked else ("success" if valid else "error")
    state = "ok" if valid else "error"
    stderr = _activity_text(str(error or "") or (test or {}).get("stderr") or (patch or {}).get("stderr") or "")
    stdout = _activity_text((test or {}).get("stdout") or (patch or {}).get("stdout") or "")
    snapshot_id = (snapshot or {}).get("id", "")
    detail = (
        f"Validated proposed diff in {workspace}; valid={'yes' if valid else 'no'}; "
        f"patch_exit_code={patch_exit}; test_exit_code={test_exit}; snapshot={snapshot_id or 'none'}."
    )
    if blocked:
        detail = f"Blocked proposed diff validation in {workspace}; {stderr or 'request rejected'}."
    record = {
        "command_id": "run-tests",
        "title": "Code Workspace Diff Validation Blocked" if blocked else ("Code Workspace Diff Validation Passed" if valid else "Code Workspace Diff Validation Failed"),
        "category": "Code",
        "status": status,
        "state": state,
        "source": "code-workspace-validate-api",
        "trust": "approval",
        "trust_mode": "ask",
        "detail": detail,
        "workspace_id": workspace_id,
        "workspace": workspace,
        "run_command": body.test_command.strip(),
        "snapshot_id": snapshot_id,
        "patch_exit_code": patch_exit,
        "test_exit_code": test_exit,
        "valid": valid,
        "stdout": stdout,
        "stderr": stderr,
        "preview": {
            "title": "Code Workspace Diff Validation",
            "intent": body.test_command.strip() or "validate proposed diff",
            "source": "code-workspace-validate-api",
            "category": "Code",
            "trust": "approval",
            "trust_label": "Approval",
            "trust_mode": "ask",
            "scope": "Temporary Code Workspace patch validation",
            "policy": "Validation creates a snapshot, tests the proposed diff, then restores before apply",
            "safety_note": "Apply remains separate and should only proceed after reviewing validation output.",
            "flags": [
                {"label": "Patch", "value": str(patch_exit), "state": "ok" if patch_exit == 0 else "error"},
                {"label": "Tests", "value": str(test_exit), "state": "ok" if test_exit == 0 else ("warn" if test_exit is None else "error")},
                {"label": "Snapshot", "value": snapshot_id or "none", "state": "ok" if snapshot_id else "warn"},
                {"label": "Restore", "value": "Attempted after validation", "state": "warn"},
            ],
        },
        "events": [{
            "at": _utc_now(),
            "status": status,
            "state": state,
            "detail": detail,
        }],
    }
    return upsert_operator_activity_record(record, owner=owner)


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
            result = {
                "ok": True,
                "workspace": code_workspace.import_archive(ws_name, tmp_path, owner=_owner(request)),
            }
            append_audit("code_workspace_imported", {
                "workspace_id": result["workspace"].get("id"),
                "name": result["workspace"].get("name"),
                "filename": file.filename or "",
                "bytes": total,
            }, user=_owner(request))
            return result
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
            return {"ok": True, **code_workspace.write_file(workspace_id, body.path, body.content, owner=_owner(request), allowed_paths=body.allowed_paths)}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/patch")
    def code_workspace_patch(workspace_id: str, body: PatchRequest, request: Request):
        try:
            result = {"ok": True, **code_workspace.apply_unified_diff(workspace_id, body.diff, owner=_owner(request), allowed_paths=body.allowed_paths)}
            append_audit("code_workspace_patch_applied", {
                "workspace_id": workspace_id,
                "exit_code": result.get("exit_code"),
                "bytes": len(body.diff.encode("utf-8")),
            }, user=_owner(request))
            return result
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/validate-diff")
    def code_workspace_validate_diff(workspace_id: str, body: ValidateDiffRequest, request: Request):
        owner = _owner(request)
        snapshot = None
        try:
            snapshot = code_workspace.create_snapshot(workspace_id, "Before proposed diff validation", owner=owner)
            patch = code_workspace.apply_unified_diff(workspace_id, body.diff, owner=owner, allowed_paths=body.allowed_paths)
            if patch.get("exit_code") != 0:
                activity = _record_validation_activity(workspace_id, body, owner, snapshot=snapshot, patch=patch, test=None)
                return {"ok": True, "valid": False, "snapshot": snapshot, "patch": patch, "test": None, "activity": activity}
            test = code_workspace.run_command(
                workspace_id,
                body.test_command,
                owner=owner,
                timeout_seconds=code_workspace.MAX_COMMAND_SECONDS,
            )
            valid = test.get("exit_code") == 0
            append_audit("code_workspace_diff_validated", {
                "workspace_id": workspace_id,
                "valid": valid,
                "test_command": body.test_command,
                "exit_code": test.get("exit_code"),
                "snapshot_id": snapshot.get("id"),
            }, user=owner)
            activity = _record_validation_activity(workspace_id, body, owner, snapshot=snapshot, patch=patch, test=test)
            return {"ok": True, "valid": valid, "snapshot": snapshot, "patch": patch, "test": test, "activity": activity}
        except code_workspace.CodeWorkspaceError as exc:
            _record_validation_activity(workspace_id, body, owner, snapshot=snapshot, error=exc)
            raise _bad(exc)
        finally:
            if snapshot:
                try:
                    code_workspace.restore_snapshot(workspace_id, snapshot["id"], owner=owner)
                except Exception:
                    pass

    @router.post("/{workspace_id}/run")
    def code_workspace_run(workspace_id: str, body: RunRequest, request: Request):
        owner = _owner(request)
        try:
            result = code_workspace.run_command(
                workspace_id,
                body.command,
                owner=owner,
                timeout_seconds=body.timeout_seconds,
            )
            activity = _record_run_activity(workspace_id, body.command, result, owner)
            return {
                "ok": True,
                **result,
                "activity": activity,
            }
        except code_workspace.CodeWorkspaceError as exc:
            _record_run_activity(
                workspace_id,
                body.command,
                {"stdout": "", "stderr": str(exc), "exit_code": 1, "runner": "validation"},
                owner,
                blocked=True,
            )
            raise _bad(exc)

    @router.post("/{workspace_id}/agent")
    async def code_workspace_agent_run(workspace_id: str, body: AgentRequest, request: Request):
        owner = _owner(request)
        try:
            result = await code_workspace_agent.run_agent(
                workspace_id,
                body.task,
                owner=owner,
                model_key=body.model_key,
                test_command=body.test_command,
                max_rounds=body.max_rounds,
                selected_paths=body.selected_paths,
                allowed_paths=body.allowed_paths,
                apply_changes=body.apply_changes,
            )
            activity = _record_agent_activity(workspace_id, body, result, owner)
            append_audit("code_workspace_agent_draft", {
                "workspace_id": workspace_id,
                "model": result.get("model"),
                "selected_paths": result.get("selected_paths", []),
                "has_proposed_diff": bool(result.get("proposed_diff")),
                "applied": bool(result.get("applied")),
            }, user=owner)
            return {**result, "activity": activity}
        except code_workspace.CodeWorkspaceError as exc:
            _record_agent_activity(workspace_id, body, None, owner, error=exc)
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

    @router.get("/{workspace_id}/snapshots")
    def code_workspace_snapshots(workspace_id: str, request: Request):
        try:
            return {"ok": True, "snapshots": code_workspace.list_snapshots(workspace_id, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/snapshots")
    def code_workspace_snapshot(workspace_id: str, body: SnapshotRequest, request: Request):
        try:
            return {"ok": True, "snapshot": code_workspace.create_snapshot(workspace_id, body.label, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.post("/{workspace_id}/snapshots/{snapshot_id}/restore")
    def code_workspace_restore_snapshot(workspace_id: str, snapshot_id: str, request: Request):
        try:
            result = {"ok": True, **code_workspace.restore_snapshot(workspace_id, snapshot_id, owner=_owner(request))}
            append_audit("code_workspace_snapshot_restored", {"workspace_id": workspace_id, "snapshot_id": snapshot_id}, user=_owner(request))
            return result
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.get("/{workspace_id}/snapshots/{snapshot_id}/diff")
    def code_workspace_diff_snapshot(workspace_id: str, snapshot_id: str, request: Request):
        try:
            return {"ok": True, **code_workspace.diff_snapshot(workspace_id, snapshot_id, owner=_owner(request))}
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    @router.get("/{workspace_id}/export")
    def code_workspace_export(workspace_id: str, request: Request):
        try:
            export = code_workspace.export_workspace(workspace_id, owner=_owner(request))
            append_audit("code_workspace_exported", {
                "workspace_id": workspace_id,
                "filename": export.get("filename"),
                "size": export.get("size"),
            }, user=_owner(request))
            return FileResponse(export["path"], filename=export["filename"], media_type="application/zip")
        except code_workspace.CodeWorkspaceError as exc:
            raise _bad(exc)

    return router
