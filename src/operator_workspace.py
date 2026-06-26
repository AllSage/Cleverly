"""Read-only local workspace and knowledge workbench evidence for Cleverly."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


ENTRY_POINTS = (
    ("dashboard", "dash", "Dashboard workspace control", "open-code-workspace-map"),
    ("text", "text", "Typed workspace request", "open-code-workspace-map"),
    ("palette", "pal", "Palette workspace route", "open-command-palette"),
    ("voice", "voice", "Voice workspace route", "open-voice-preflight"),
    ("workflow", "flow", "Workflow workspace handoff", "open-automation-map"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _count(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state(*states: str) -> str:
    clean = [str(item or "").lower() for item in states if item]
    if "error" in clean:
        return "error"
    if "warn" in clean or "loading" in clean:
        return "warn"
    return "ok"


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(plan.get("summary"))


def _summary_state(plan: dict[str, Any], default: str = "warn") -> str:
    summary = _summary(plan)
    return _trim(summary.get("state") or plan.get("state") or default, 40).lower() or default


def _row(row_id: str, state: str, badge: str, title: str, detail: str, action: str, action_label: str = "Open") -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state if state in {"ok", "warn", "error", "loading"} else "warn",
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "requires_approval": state != "ok",
        "runs_tests": False,
        "runs_build": False,
        "starts_build_watch": False,
        "runs_search": False,
        "starts_research": False,
        "uploads_files": False,
        "generates_images": False,
        "writes_files": False,
        "deletes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }


def _code_row(code_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(code_plan)
    workspace_count = _count(summary.get("workspace_count") or summary.get("code_workspace_count"))
    candidate_count = _count(summary.get("candidate_command_count"))
    state = _summary_state(code_plan, "ok" if workspace_count else "warn")
    return _row(
        "workspace-code",
        state,
        "code",
        "Code workspaces",
        f"{workspace_count} workspace(s); {candidate_count} candidate test command(s); test execution remains approval-gated.",
        "run-tests",
        "Tests",
    )


def _build_row(build_watch_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(build_watch_plan)
    state = _summary_state(build_watch_plan)
    return _row(
        "workspace-build-watch",
        state,
        "build",
        "Build watch and repo monitoring",
        f"{_count(summary.get('watchable_workspace_count') or summary.get('workspace_count'))} watchable workspace(s); build loops are not started by this plan.",
        "watch-build-until-green",
        "Watch",
    )


def _document_row(document_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(document_plan)
    document_count = _count(summary.get("document_count") or summary.get("total_documents"))
    state = _summary_state(document_plan, "ok" if document_count else "warn")
    return _row(
        "workspace-documents",
        state,
        "docs",
        "Local document search",
        f"{document_count} indexed document(s); local search and indexing stay behind the Library/RAG controls.",
        "open-documents-preflight",
        "Library",
    )


def _research_row(research_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(research_plan)
    state = _summary_state(research_plan)
    return _row(
        "workspace-research",
        state,
        "search",
        "Research and SearXNG boundary",
        f"{_count(summary.get('active_job_count'))} active job(s); {_count(summary.get('saved_report_count') or summary.get('report_count'))} saved report(s); web research is network-gated.",
        "open-research-preflight",
        "Research",
    )


def _gallery_row(gallery_plan: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(gallery_plan)
    image_count = _count(summary.get("image_count") or summary.get("gallery_count") or summary.get("media_count"))
    state = _summary_state(gallery_plan, "ok" if image_count else "warn")
    return _row(
        "workspace-gallery",
        state,
        "media",
        "Gallery and generated media",
        f"{image_count} local media item(s); uploads and generation require explicit media controls.",
        "open-gallery",
        "Gallery",
    )


def _file_row(file_ops_plan: dict[str, Any], data_plan: dict[str, Any]) -> dict[str, Any]:
    file_summary = _summary(file_ops_plan)
    data_summary = _summary(data_plan)
    state = _state(_summary_state(file_ops_plan), _summary_state(data_plan, "ok"))
    return _row(
        "workspace-file-data-boundary",
        state,
        "files",
        "File and local data boundary",
        f"{_count(file_summary.get('root_count') or file_summary.get('file_root_count'))} file root(s); {_count(data_summary.get('data_root_count') or data_summary.get('path_count'))} data path(s); writes/deletes stay approval-gated.",
        "open-local-data-map",
        "Data",
    )


def _entry_rows(ready: bool) -> list[dict[str, Any]]:
    rows = []
    for entry, badge, title, action in ENTRY_POINTS:
        rows.append({
            "id": f"workspace-{entry}-route",
            "entry": entry,
            "state": "ok" if ready else "warn",
            "badge": badge,
            "title": title,
            "detail": "Workspace requests show code, document, research, gallery, file, data, and approval gates before local work starts.",
            "action": action,
            "actionLabel": "Open",
            "ready": ready,
            "workspace_api": "/api/operator/workspace-plan",
            "code_api": "/api/operator/code-test-plan",
            "build_watch_api": "/api/operator/build-watch-plan",
            "document_api": "/api/operator/document-search-plan",
            "research_api": "/api/operator/research-plan",
            "gallery_api": "/api/operator/gallery-plan",
            "file_ops_api": "/api/operator/file-ops-plan",
            "data_api": "/api/operator/data-plan",
            "requires_approval": not ready,
            "runs_tests": False,
            "starts_build_watch": False,
            "runs_search": False,
            "starts_research": False,
            "uploads_files": False,
            "generates_images": False,
            "writes_files": False,
            "deletes_files": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return rows


def _handoff_row(
    row_id: str,
    state: str,
    badge: str,
    title: str,
    detail: str,
    action: str,
    action_label: str,
    *,
    target_api: str,
    approval_command_id: str = "open-code-workspace-map",
    requires_approval: bool = True,
    runs_tests: bool = False,
    starts_build_watch: bool = False,
    runs_search: bool = False,
    starts_research: bool = False,
    uploads_files: bool = False,
    generates_images: bool = False,
    writes_files: bool = False,
    deletes_files: bool = False,
    runs_shell: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state if state in {"ok", "warn", "error", "loading"} else "warn",
        "badge": badge,
        "title": title,
        "detail": detail,
        "action": action,
        "actionLabel": action_label,
        "target_api": target_api,
        "approval_command_id": approval_command_id,
        "requires_approval": requires_approval,
        "executes": False,
        "runs_tests": False,
        "runs_build": False,
        "starts_build_watch": False,
        "runs_search": False,
        "starts_research": False,
        "uploads_files": False,
        "generates_images": False,
        "writes_files": False,
        "deletes_files": False,
        "runs_shell": False,
        "uses_network": False,
        "gated_operation": {
            "runs_tests": runs_tests,
            "starts_build_watch": starts_build_watch,
            "runs_search": runs_search,
            "starts_research": starts_research,
            "uploads_files": uploads_files,
            "generates_images": generates_images,
            "writes_files": writes_files,
            "deletes_files": deletes_files,
            "runs_shell": runs_shell,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    *,
    ready: bool,
    workspace_rows: list[dict[str, Any]],
    code_plan: dict[str, Any],
    build_watch_plan: dict[str, Any],
    document_plan: dict[str, Any],
    research_plan: dict[str, Any],
    gallery_plan: dict[str, Any],
    file_ops_plan: dict[str, Any],
    data_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries = {
        "code": _summary(code_plan),
        "build": _summary(build_watch_plan),
        "documents": _summary(document_plan),
        "research": _summary(research_plan),
        "gallery": _summary(gallery_plan),
        "files": _summary(file_ops_plan),
        "data": _summary(data_plan),
    }
    row_states = {row.get("id"): row.get("state") for row in workspace_rows}
    code_ready = row_states.get("workspace-code") == "ok"
    build_ready = row_states.get("workspace-build-watch") == "ok"
    document_ready = row_states.get("workspace-documents") == "ok"
    research_ready = row_states.get("workspace-research") == "ok"
    gallery_ready = row_states.get("workspace-gallery") == "ok"
    file_ready = row_states.get("workspace-file-data-boundary") == "ok"
    data_ready = _summary_state(data_plan, "warn") == "ok"
    workspace_count = _count(summaries["code"].get("workspace_count") or summaries["code"].get("code_workspace_count"))
    candidate_count = _count(summaries["code"].get("candidate_command_count"))
    document_count = _count(summaries["documents"].get("document_count") or summaries["documents"].get("total_documents"))
    report_count = _count(summaries["research"].get("saved_report_count") or summaries["research"].get("report_count"))
    media_count = _count(summaries["gallery"].get("media_file_count") or summaries["gallery"].get("image_count") or summaries["gallery"].get("gallery_count"))
    root_count = _count(summaries["files"].get("root_count") or summaries["files"].get("file_root_count"))
    data_count = _count(summaries["data"].get("data_root_count") or summaries["data"].get("path_count"))
    return [
        _handoff_row(
            "workspace-code-test-handoff",
            "ok" if code_ready and candidate_count else "warn",
            "tests",
            "Code test execution handoff",
            f"{workspace_count} workspace(s) and {candidate_count} candidate command(s) are visible; exact commands run only from Code Workspace controls.",
            "run-tests",
            "Tests",
            target_api="/api/code-workspaces/{id}/run",
            runs_tests=True,
            runs_shell=True,
        ),
        _handoff_row(
            "workspace-build-watch-handoff",
            "ok" if build_ready else "warn",
            "watch",
            "Build-watch loop handoff",
            "Repeated build monitoring remains a workflow action after workspace, command, and retry scope review.",
            "watch-build-until-green",
            "Watch",
            target_api="/api/operator/build-watch-plan",
            starts_build_watch=True,
            runs_shell=True,
        ),
        _handoff_row(
            "workspace-document-search-handoff",
            "ok" if document_ready and document_count else "warn",
            "docs",
            "Local document search handoff",
            f"{document_count} indexed document(s) are visible; query execution stays in Library/RAG controls.",
            "search-local-documents",
            "Search",
            target_api="/api/personal/search",
            approval_command_id="open-library-preflight",
            runs_search=True,
        ),
        _handoff_row(
            "workspace-research-escalation-handoff",
            "ok" if research_ready else "warn",
            "web",
            "Research escalation handoff",
            f"{report_count} saved report(s) are visible; web/SearXNG research requires explicit network policy review.",
            "open-research-preflight",
            "Research",
            target_api="/api/research/start",
            approval_command_id="open-research-preflight",
            starts_research=True,
            uses_network=True,
        ),
        _handoff_row(
            "workspace-media-operation-handoff",
            "ok" if gallery_ready else "warn",
            "media",
            "Media operation handoff",
            f"{media_count} local media item(s) are visible; upload, generation, edit, export, and delete actions stay in Gallery gates.",
            "open-library-preflight",
            "Media",
            target_api="/api/operator/gallery-plan",
            approval_command_id="open-library-preflight",
            uploads_files=True,
            generates_images=True,
            writes_files=True,
            deletes_files=True,
        ),
        _handoff_row(
            "workspace-file-data-handoff",
            "ok" if file_ready and data_ready else "warn",
            "files",
            "File and data boundary handoff",
            f"{root_count} file root(s) and {data_count} data path(s) are visible; file writes and deletes require File Ops approval.",
            "open-local-data-map",
            "Data",
            target_api="/api/operator/file-ops-plan",
            approval_command_id="open-local-data-map",
            writes_files=True,
            deletes_files=True,
        ),
        _handoff_row(
            "workspace-backup-recovery-handoff",
            "ok" if ready else "warn",
            "safe",
            "Backup and rollback handoff",
            "Risky workspace operations should pass backup, snapshot, retry, and rollback review before execution.",
            "open-backup-preflight",
            "Backup",
            target_api="/api/operator/recovery-plan",
            approval_command_id="open-backup-preflight",
            writes_files=True,
        ),
        _handoff_row(
            "workspace-activity-handoff",
            "ok",
            "log",
            "Activity and retry handoff",
            "Approved workspace work should write result, log, retry, recovery, and rollback references into the local activity timeline.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
            requires_approval=False,
        ),
    ]


def _alert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in rows:
        if row.get("state") not in {"warn", "error"}:
            continue
        alerts.append({
            "id": f"workspace-alert-{row['id']}",
            "state": row.get("state"),
            "badge": row.get("badge") or "work",
            "title": row.get("title"),
            "detail": row.get("detail"),
            "action": row.get("action") or "open-code-workspace-map",
            "actionLabel": "Review",
            "requires_approval": row.get("state") == "error",
            "runs_tests": False,
            "runs_search": False,
            "writes_files": False,
            "runs_shell": False,
            "uses_network": False,
        })
    return alerts[:12]


def _api_action(path: str, title: str, *, writes: bool = False, starts: bool = False, network: bool = False, deletes: bool = False) -> dict[str, Any]:
    return {
        "path": path,
        "method": "GET" if not writes and not starts and not deletes else "POST",
        "title": title,
        "state": "warn" if writes or starts or network or deletes else "ok",
        "writes": writes,
        "deletes_files": deletes,
        "starts_jobs": starts,
        "runs_tests": starts and "code" in path,
        "runs_search": starts and ("search" in path or "research" in path),
        "uploads_files": writes and "gallery" in path,
        "runs_shell": False,
        "uses_network": network,
        "requires_approval": writes or starts or network or deletes,
    }


def run_operator_workspace_plan(
    owner: str = "local",
    *,
    code_plan: dict[str, Any] | None = None,
    build_watch_plan: dict[str, Any] | None = None,
    document_plan: dict[str, Any] | None = None,
    research_plan: dict[str, Any] | None = None,
    gallery_plan: dict[str, Any] | None = None,
    file_ops_plan: dict[str, Any] | None = None,
    data_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one read-only workbench view across local code, files, knowledge, and media."""
    code_plan = _as_dict(code_plan)
    build_watch_plan = _as_dict(build_watch_plan)
    document_plan = _as_dict(document_plan)
    research_plan = _as_dict(research_plan)
    gallery_plan = _as_dict(gallery_plan)
    file_ops_plan = _as_dict(file_ops_plan)
    data_plan = _as_dict(data_plan)
    workspace_rows = [
        _code_row(code_plan),
        _build_row(build_watch_plan),
        _document_row(document_plan),
        _research_row(research_plan),
        _gallery_row(gallery_plan),
        _file_row(file_ops_plan, data_plan),
    ]
    state = _state(*(row.get("state") for row in workspace_rows))
    ready = state == "ok"
    entry_rows = _entry_rows(ready)
    handoff_rows = _handoff_rows(
        ready=ready,
        workspace_rows=workspace_rows,
        code_plan=code_plan,
        build_watch_plan=build_watch_plan,
        document_plan=document_plan,
        research_plan=research_plan,
        gallery_plan=gallery_plan,
        file_ops_plan=file_ops_plan,
        data_plan=data_plan,
    )
    alert_rows = _alert_rows(workspace_rows)
    summary = {
        "workspace_row_count": len(workspace_rows),
        "workspace_ready_count": sum(1 for row in workspace_rows if row.get("state") == "ok"),
        "entry_route_count": len(entry_rows),
        "entry_route_ready_count": sum(1 for row in entry_rows if row.get("ready") is True),
        "handoff_count": len(handoff_rows),
        "handoff_ready_count": sum(1 for row in handoff_rows if row.get("state") == "ok"),
        "workspace_alert_count": len(alert_rows),
        "critical_workspace_alert_count": sum(1 for row in alert_rows if row.get("state") == "error"),
        "state": state,
        "runs_tests": False,
        "runs_build": False,
        "starts_build_watch": False,
        "runs_search": False,
        "starts_research": False,
        "uploads_files": False,
        "generates_images": False,
        "writes_files": False,
        "deletes_files": False,
        "runs_shell": False,
        "uses_network": False,
    }
    return {
        "mode": "read-only-local-workspace-plan",
        "owner": owner,
        "generated_at": _utc_now(),
        "state": state,
        "summary": summary,
        "workspace_rows": workspace_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "alert_rows": alert_rows,
        "api_actions": [
            _api_action("/api/operator/workspace-plan", "Read local workspace workbench readiness"),
            _api_action("/api/operator/code-test-plan", "Read code test gates"),
            _api_action("/api/operator/build-watch-plan", "Read build-watch gates"),
            _api_action("/api/operator/document-search-plan", "Read local document search gates"),
            _api_action("/api/operator/research-plan", "Read research and SearXNG gates"),
            _api_action("/api/operator/gallery-plan", "Read gallery and media gates"),
            _api_action("/api/operator/file-ops-plan", "Read file-operation gates"),
            _api_action("/api/operator/data-plan", "Read local data map gates"),
            _api_action("/api/code-workspaces/{id}/run", "Run a workspace command", starts=True),
            _api_action("/api/research/start", "Start a research job", starts=True, network=True),
            _api_action("/api/gallery/upload", "Upload local media", writes=True),
            _api_action("/api/files/delete", "Delete local files", deletes=True),
        ],
        "paths": {
            "code": "data/code_workspace/",
            "documents": "data/personal_docs/",
            "rag_index": "data/personal_docs/index",
            "research": "data/deep_research/",
            "gallery": "data/gallery/",
            "uploads": "data/uploads/",
            "generated_images": "data/generated_images/",
            "activity": "data/operator_activity.json",
        },
        "approval": {
            "required": False,
            "policy": (
                "This endpoint only audits local workspace and knowledge readiness. It does not run tests, "
                "start build watches, search documents, start research, upload files, generate images, write files, "
                "delete files, run shell commands, or use network access."
            ),
            "disallowed_actions": [
                "run tests",
                "start build watches",
                "search documents",
                "start research",
                "upload files",
                "generate images",
                "write files",
                "delete files",
                "run shell commands",
                "use network access",
            ],
        },
    }
