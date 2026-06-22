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
        "route_rows": route_rows,
        "guard_rows": guard_rows,
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
