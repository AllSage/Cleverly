"""Read-only local document search planning for the Cleverly operator console."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, PERSONAL_DIR


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _path_state(path: Path) -> str:
    try:
        if path.exists():
            return "ok"
    except OSError:
        return "warn"
    return "warn"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return fallback


def _manager_index(personal_docs_manager: Any) -> list[dict[str, Any]]:
    index = getattr(personal_docs_manager, "index", []) if personal_docs_manager is not None else []
    return [item for item in index if isinstance(item, dict)]


def _manager_dirs(personal_docs_manager: Any, personal_dir: Path) -> list[str]:
    if personal_docs_manager is not None and hasattr(personal_docs_manager, "get_indexed_directories"):
        try:
            values = personal_docs_manager.get_indexed_directories() or []
            return [_trim(value, 1000) for value in values if _trim(value, 1000)]
        except Exception:
            return []
    values = _read_json(personal_dir / "indexed_directories.json", [])
    return [_trim(value, 1000) for value in values if _trim(value, 1000)] if isinstance(values, list) else []


def _manager_stats(personal_docs_manager: Any, index: list[dict[str, Any]], dirs: list[str], personal_dir: Path) -> dict[str, Any]:
    if personal_docs_manager is not None and hasattr(personal_docs_manager, "get_stats"):
        try:
            stats = personal_docs_manager.get_stats() or {}
            if isinstance(stats, dict):
                return stats
        except Exception:
            pass
    return {
        "total_documents": len(index),
        "total_chunks": sum(len(item.get("chunks") or []) for item in index),
        "total_size_bytes": sum(int(item.get("size") or 0) for item in index),
        "directories_count": len(dirs) + 1,
        "base_directory": str(personal_dir),
        "additional_directories": dirs,
    }


def _rag_stats(rag_manager: Any) -> dict[str, Any]:
    if rag_manager is None:
        return {"available": False, "state": "warn", "detail": "RAG manager is not available"}
    try:
        stats = rag_manager.get_stats() if hasattr(rag_manager, "get_stats") else {}
        stats = stats if isinstance(stats, dict) else {}
        return {
            "available": True,
            "state": "ok",
            "detail": stats.get("status") or stats.get("detail") or "RAG stats endpoint available",
            "embedding_model": stats.get("embedding_model") or stats.get("model") or "",
            "total_documents": stats.get("total_documents") or stats.get("documents") or stats.get("document_count"),
            "chunks": stats.get("chunks") or stats.get("vector_count") or stats.get("count"),
        }
    except Exception as exc:
        return {
            "available": False,
            "state": "warn",
            "detail": f"RAG stats unavailable: {_trim(exc, 160)}",
        }


def _sample_rows(index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in index[:6]:
        chunks = item.get("chunks") or []
        rows.append({
            "id": _trim(item.get("path") or item.get("name") or "document", 240),
            "state": "ok",
            "badge": "doc",
            "title": _trim(item.get("name") or Path(str(item.get("path") or "Document")).name, 160),
            "detail": f"{int(item.get('size') or 0)} bytes; {len(chunks)} indexed chunk{'s' if len(chunks) != 1 else ''}",
            "path": _trim(item.get("path"), 1000),
            "action": "open-documents-preflight",
            "actionLabel": "Files",
        })
    return rows


def _entry_rows(doc_count: int, keyword_ready: bool, vector_ready: bool) -> list[dict[str, Any]]:
    ready = keyword_ready or vector_ready
    readiness_detail = (
        "Local Document Search opens with vector or keyword retrieval evidence ready; query text is still required before search."
        if ready
        else "Local Document Search opens first and shows index/RAG readiness gaps before a query is useful."
    )
    workflow_detail = (
        "Workflow handoff can route to local Library search, but web research stays separate and network-gated."
        if ready
        else "Workflow handoff stays in review mode until local documents, chunks, or RAG evidence are available."
    )
    return [
        {
            "id": "document-search-dashboard-entry",
            "entry": "dashboard",
            "state": "ok" if ready else "warn",
            "badge": "dash",
            "title": "Command Center dashboard",
            "detail": readiness_detail,
            "command_id": "search-local-documents",
            "action": "search-local-documents",
            "actionLabel": "Search",
            "requires_query": True,
            "executes": False,
            "uses_network": False,
        },
        {
            "id": "document-search-text-entry",
            "entry": "text",
            "state": "ok",
            "badge": "text",
            "title": "Typed operator command",
            "detail": "The phrase 'Search my local documents for this' opens local search and requires query text before retrieval.",
            "command_id": "search-local-documents",
            "action": "search-local-documents",
            "actionLabel": "Search",
            "requires_query": True,
            "executes": False,
            "uses_network": False,
        },
        {
            "id": "document-search-palette-entry",
            "entry": "palette",
            "state": "ok",
            "badge": "cmd",
            "title": "Global command palette",
            "detail": "The palette exposes Search Local Documents as a local-only Library route, not a web research route.",
            "command_id": "search-local-documents",
            "action": "open-command-palette",
            "actionLabel": "Palette",
            "requires_query": True,
            "executes": False,
            "uses_network": False,
        },
        {
            "id": "document-search-voice-entry",
            "entry": "voice",
            "state": "ok",
            "badge": "voice",
            "title": "Voice command mode",
            "detail": "Voice routing can open the same local search surface without sending documents or queries to network services.",
            "command_id": "search-local-documents",
            "action": "open-voice-preflight",
            "actionLabel": "Voice",
            "requires_query": True,
            "executes": False,
            "uses_network": False,
        },
        {
            "id": "document-search-workflow-entry",
            "entry": "workflow",
            "state": "ok" if ready and doc_count else "warn",
            "badge": "flow",
            "title": "Automation workflow handoff",
            "detail": workflow_detail,
            "command_id": "search-local-documents",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
            "requires_query": True,
            "executes": False,
            "uses_network": False,
        },
    ]


def _document_alert_rows(
    *,
    doc_count: int,
    chunk_count: int,
    directory_count: int,
    excluded_count: int,
    rag: dict[str, Any],
    keyword_ready: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if doc_count < 1:
        rows.append(
            {
                "id": "document-index-empty",
                "state": "warn",
                "badge": "docs",
                "title": "Local document index is empty",
                "detail": "Add or index local documents before relying on local document search.",
                "action": "open-documents-preflight",
                "actionLabel": "Files",
                "requires_approval": True,
            }
        )
    if doc_count and chunk_count < 1:
        rows.append(
            {
                "id": "keyword-chunks-missing",
                "state": "warn",
                "badge": "idx",
                "title": "Keyword chunks missing",
                "detail": "Documents are visible, but no keyword chunks are indexed for fallback search.",
                "action": "open-documents-preflight",
                "actionLabel": "Files",
                "requires_approval": True,
            }
        )
    if not rag.get("available"):
        rows.append(
            {
                "id": "vector-search-unavailable",
                "state": "warn",
                "badge": "rag",
                "title": "Vector search unavailable",
                "detail": rag.get("detail") or "RAG manager or Chroma index is unavailable; keyword fallback may still work.",
                "action": "open-embedding-preflight",
                "actionLabel": "RAG",
                "requires_approval": False,
            }
        )
    if not keyword_ready:
        rows.append(
            {
                "id": "keyword-fallback-not-ready",
                "state": "warn",
                "badge": "key",
                "title": "Keyword fallback not ready",
                "detail": f"{doc_count} document(s) and {chunk_count} chunk(s) visible; local search may have no fallback results.",
                "action": "open-documents-preflight",
                "actionLabel": "Files",
                "requires_approval": False,
            }
        )
    if directory_count < 1:
        rows.append(
            {
                "id": "tracked-directory-missing",
                "state": "warn",
                "badge": "dirs",
                "title": "No tracked document directory",
                "detail": "No local directory is registered for document indexing.",
                "action": "open-documents-preflight",
                "actionLabel": "Files",
                "requires_approval": True,
            }
        )
    if excluded_count:
        rows.append(
            {
                "id": "excluded-files-present",
                "state": "loading",
                "badge": "skip",
                "title": "Excluded files need review",
                "detail": f"{excluded_count} local file(s) are excluded from document search.",
                "action": "open-documents-preflight",
                "actionLabel": "Files",
                "requires_approval": False,
            }
        )
    rows.append(
        {
            "id": "index-write-gate",
            "state": "warn",
            "badge": "gate",
            "title": "Index changes require review",
            "detail": "Reloading, adding directories, excluding files, rebuilding RAG, and deleting documents remain explicit Library actions.",
            "action": "open-documents-preflight",
            "actionLabel": "Files",
            "requires_approval": True,
        }
    )
    return rows[:12]


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
    approval_command_id: str = "open-documents-preflight",
    requires_approval: bool = False,
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
        "runs_search": False,
        "reads_query": False,
        "reads_result_snippets": False,
        "indexes_files": False,
        "changes_files": False,
        "adds_directories": False,
        "excludes_files": False,
        "rebuilds_rag": False,
        "starts_research": False,
        "writes_activity": False,
        "uses_network": False,
    }


def _handoff_rows(
    *,
    doc_count: int,
    chunk_count: int,
    directory_count: int,
    excluded_count: int,
    vector_ready: bool,
    keyword_ready: bool,
) -> list[dict[str, Any]]:
    search_state = "ok" if doc_count and (vector_ready or keyword_ready) else "warn"
    vector_state = "ok" if vector_ready else "warn"
    keyword_state = "ok" if keyword_ready else "warn"
    directory_state = "ok" if directory_count else "warn"
    exclusion_state = "warn" if excluded_count else "ok"
    return [
        _handoff_row(
            "document-query-review-handoff",
            search_state,
            "query",
            "Query review handoff",
            f"{doc_count} document(s) and {chunk_count} chunk(s) are visible; query text is required before retrieval starts.",
            "search-local-documents",
            "Search",
            target_api="/api/personal/search",
        ),
        _handoff_row(
            "document-vector-keyword-handoff",
            "ok" if vector_ready or keyword_ready else "warn",
            "rag",
            "Vector and keyword retrieval handoff",
            f"vector_ready={vector_ready}; keyword_ready={keyword_ready}; retrieval stays local to RAG and personal document chunks.",
            "open-embedding-preflight",
            "RAG",
            target_api="/api/rag/stats",
        ),
        _handoff_row(
            "document-index-refresh-handoff",
            "warn",
            "idx",
            "Index refresh handoff",
            "Reloading indexes, rebuilding RAG, or refreshing personal document chunks requires explicit Library review.",
            "open-documents-preflight",
            "Files",
            target_api="/api/personal/reload",
            requires_approval=True,
        ),
        _handoff_row(
            "document-directory-scope-handoff",
            directory_state,
            "dirs",
            "Directory scope handoff",
            f"{directory_count} tracked directory entry/entries are visible; adding external directories stays owner-selected.",
            "open-documents-preflight",
            "Files",
            target_api="/api/operator/document-search-plan",
            requires_approval=directory_count < 1,
        ),
        _handoff_row(
            "document-exclusion-handoff",
            exclusion_state,
            "skip",
            "Exclusion and file boundary handoff",
            f"{excluded_count} excluded file(s) are recorded; include/exclude changes are separate local Library actions.",
            "open-local-data-map",
            "Data",
            target_api="/api/operator/file-ops-plan",
            requires_approval=bool(excluded_count),
        ),
        _handoff_row(
            "document-research-escalation-handoff",
            "ok" if doc_count else "warn",
            "web",
            "Research escalation handoff",
            "Local document search should be reviewed before escalating to network-capable Deep Research.",
            "open-research-preflight",
            "Research",
            target_api="/api/operator/research-plan",
        ),
        _handoff_row(
            "document-activity-handoff",
            "ok",
            "log",
            "Activity logging handoff",
            "Completed local document searches should write only query metadata, result count, route type, and result references to activity.",
            "open-activity-preflight",
            "Activity",
            target_api="/api/operator/activity",
        ),
    ]


def run_operator_document_search_plan(
    owner: str = "local",
    *,
    personal_docs_manager: Any = None,
    rag_manager: Any = None,
    data_root: str | Path | None = None,
    personal_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a read-only plan for local document search routing."""
    owner = owner or "local"
    data_path = Path(data_root) if data_root is not None else Path(os.getenv("DATA_DIR") or DATA_DIR)
    personal_path = Path(personal_dir) if personal_dir is not None else Path(PERSONAL_DIR)
    index = _manager_index(personal_docs_manager)
    dirs = _manager_dirs(personal_docs_manager, personal_path)
    stats = _manager_stats(personal_docs_manager, index, dirs, personal_path)
    rag = _rag_stats(rag_manager if rag_manager is not None else getattr(personal_docs_manager, "rag_manager", None))
    excluded = _read_json(personal_path / "excluded_files.json", [])
    excluded_count = len(excluded) if isinstance(excluded, list) else 0
    doc_count = int(stats.get("total_documents") or len(index) or 0)
    chunk_count = int(stats.get("total_chunks") or sum(len(item.get("chunks") or []) for item in index) or 0)
    directory_count = int(stats.get("directories_count") or (len(dirs) + 1))
    keyword_ready = doc_count > 0 and chunk_count > 0
    state = "ok" if keyword_ready or rag.get("available") else "warn"
    alert_rows = _document_alert_rows(
        doc_count=doc_count,
        chunk_count=chunk_count,
        directory_count=directory_count,
        excluded_count=excluded_count,
        rag=rag,
        keyword_ready=keyword_ready,
    )
    entry_rows = _entry_rows(doc_count, keyword_ready, bool(rag.get("available")))
    handoff_rows = _handoff_rows(
        doc_count=doc_count,
        chunk_count=chunk_count,
        directory_count=directory_count,
        excluded_count=excluded_count,
        vector_ready=bool(rag.get("available")),
        keyword_ready=keyword_ready,
    )

    index_rows = [
        {
            "id": "personal-documents",
            "state": _path_state(personal_path),
            "badge": "docs",
            "title": "Personal documents root",
            "detail": f"{doc_count} indexed document{'s' if doc_count != 1 else ''}; {chunk_count} chunk{'s' if chunk_count != 1 else ''}",
            "path": str(personal_path),
            "action": "open-documents-preflight",
            "actionLabel": "Files",
        },
        {
            "id": "tracked-directories",
            "state": "ok" if directory_count else "warn",
            "badge": "dirs",
            "title": "Tracked local directories",
            "detail": f"{directory_count} director{'ies' if directory_count != 1 else 'y'} visible to the local index",
            "path": str(personal_path / "indexed_directories.json"),
            "action": "open-library-preflight",
            "actionLabel": "Library",
        },
        {
            "id": "excluded-files",
            "state": "ok" if excluded_count == 0 else "warn",
            "badge": "skip",
            "title": "Excluded local files",
            "detail": f"{excluded_count} excluded file{'s' if excluded_count != 1 else ''} recorded",
            "path": str(personal_path / "excluded_files.json"),
            "action": "open-documents-preflight",
            "actionLabel": "Files",
        },
    ]
    index_rows.extend(_sample_rows(index))

    route_rows = [
        {
            "id": "open-search",
            "state": "ok",
            "badge": "ui",
            "title": "Open local search modal",
            "detail": "Command Center opens the local document search surface first.",
            "action": "search-local-documents",
            "actionLabel": "Search",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "vector-first",
            "state": "ok" if rag.get("available") else "warn",
            "badge": "rag",
            "title": "Vector search first",
            "detail": rag.get("detail") or "RAG stats unavailable",
            "action": "open-embedding-preflight",
            "actionLabel": "RAG",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "keyword-fallback",
            "state": "ok" if keyword_ready else "warn",
            "badge": "key",
            "title": "Keyword fallback",
            "detail": "Falls back to local chunks from personal_docs when vector retrieval has no usable match.",
            "action": "open-documents-preflight",
            "actionLabel": "Files",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "no-match",
            "state": "ok",
            "badge": "none",
            "title": "No-match behavior",
            "detail": "Reports that no matching indexed local documents were found instead of claiming no local access.",
            "action": "search-local-documents",
            "actionLabel": "Search",
            "executes": False,
            "requires_approval": False,
        },
    ]

    guard_rows = [
        {
            "id": "read-only-plan",
            "state": "ok",
            "badge": "plan",
            "title": "Plan does not run search",
            "detail": "This endpoint inventories local search readiness only; it does not submit a query or read result snippets.",
            "executes": False,
            "requires_approval": False,
            "action": "open-trust-controls",
            "actionLabel": "Trust",
        },
        {
            "id": "local-only",
            "state": "ok",
            "badge": "local",
            "title": "Local-only retrieval",
            "detail": "Search routes to personal documents and local RAG/keyword indexes; web research remains a separate network-gated workflow.",
            "executes": False,
            "requires_approval": False,
            "action": "open-offline",
            "actionLabel": "Policy",
        },
        {
            "id": "index-changes",
            "state": "warn",
            "badge": "edit",
            "title": "Index changes require action",
            "detail": "Reloading, adding directories, excluding files, or rebuilding RAG are separate user actions.",
            "executes": False,
            "requires_approval": True,
            "action": "open-documents-preflight",
            "actionLabel": "Files",
        },
    ]

    api_actions = [
        {
            "id": "operator-document-search-plan",
            "method": "GET",
            "path": "/api/operator/document-search-plan",
            "risk": "read-only",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "search-personal-documents",
            "method": "GET",
            "path": "/api/personal/search",
            "risk": "local-read-query",
            "executes": False,
            "requires_query": True,
            "requires_approval": False,
        },
        {
            "id": "document-library",
            "method": "GET",
            "path": "/api/documents/library",
            "risk": "local-read",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "rag-stats",
            "method": "GET",
            "path": "/api/rag/stats",
            "risk": "local-read",
            "executes": False,
            "requires_approval": False,
        },
        {
            "id": "reload-personal-index",
            "method": "POST",
            "path": "/api/personal/reload",
            "risk": "local-index-refresh",
            "executes": False,
            "requires_approval": True,
        },
    ]

    evidence_rows = [
        {
            "id": "search-route",
            "state": "ok",
            "badge": "route",
            "title": "Search command route",
            "detail": "Target phrase routes to search-local-documents before any query is sent.",
            "action": "search-local-documents",
            "actionLabel": "Search",
        },
        {
            "id": "activity-ledger",
            "state": "ok",
            "badge": "log",
            "title": "Search activity ledger",
            "detail": "Browser search completions are mirrored to data/operator_activity.json with query metadata and result references only; result snippets are not stored.",
            "action": "open-activity-preflight",
            "actionLabel": "Activity",
        },
        {
            "id": "rag-ready",
            "state": "ok" if rag.get("available") else "warn",
            "badge": "rag",
            "title": "RAG availability",
            "detail": rag.get("detail") or "RAG stats unavailable",
            "action": "open-embedding-preflight",
            "actionLabel": "RAG",
        },
        {
            "id": "keyword-index",
            "state": "ok" if keyword_ready else "warn",
            "badge": "idx",
            "title": "Keyword fallback index",
            "detail": f"{doc_count} document{'s' if doc_count != 1 else ''}; {chunk_count} chunk{'s' if chunk_count != 1 else ''}",
            "action": "open-documents-preflight",
            "actionLabel": "Files",
        },
    ]

    return {
        "mode": "read-only-document-search-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": state,
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "directory_count": directory_count,
            "excluded_count": excluded_count,
            "vector_ready": bool(rag.get("available")),
            "keyword_ready": keyword_ready,
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "document_alert_count": len(alert_rows),
            "critical_document_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "runs_search": False,
            "reads_query": False,
            "indexes_files": False,
            "changes_files": False,
            "uses_network": False,
            "requires_search_text": True,
            "activity_metadata_only": True,
            "route_command_id": "search-local-documents",
            "next_action": "Open Local Document Search, enter query text, and review local-only vector or keyword results.",
        },
        "rag": rag,
        "index_rows": index_rows,
        "entry_rows": entry_rows,
        "route_rows": route_rows,
        "guard_rows": guard_rows,
        "alert_rows": alert_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "evidence_rows": evidence_rows,
        "approval": {
            "required": False,
            "gate": "Search text required",
            "policy": "This endpoint only prepares local document search evidence. It does not run a query, reload indexes, add directories, rebuild RAG, read result snippets, use web search, or modify files.",
            "disallowed_by_default": [
                "web search",
                "reindex directories",
                "add external directory",
                "delete documents",
                "wipe document data",
            ],
        },
        "paths": {
            "data": str(data_path),
            "personal_docs": str(personal_path),
            "personal_index": str(personal_path / "indexed_directories.json"),
            "excluded_files": str(personal_path / "excluded_files.json"),
            "native_chroma": str(data_path / "chroma"),
            "docker_chroma": "/data",
            "search_endpoint": "/api/personal/search",
            "activity": "data/operator_activity.json",
        },
    }
