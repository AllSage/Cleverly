#!/usr/bin/env python
"""Smoke read-only operator routes inside the Cleverly runtime.

This is intentionally runtime-oriented: it imports the app's route wiring and
calls the GET endpoints directly with a minimal request object. It verifies that
the main operator surfaces load, return dictionaries, and do not advertise
execution/network/write behavior in their summary flags.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


OPERATOR_PREFIX = "/api/operator"

SMOKE_PATHS = [
    f"{OPERATOR_PREFIX}/checks",
    f"{OPERATOR_PREFIX}/services",
    f"{OPERATOR_PREFIX}/services-plan",
    f"{OPERATOR_PREFIX}/docker-runtime-plan",
    f"{OPERATOR_PREFIX}/credentials-plan",
    f"{OPERATOR_PREFIX}/data-plan",
    f"{OPERATOR_PREFIX}/repair-plan",
    f"{OPERATOR_PREFIX}/recovery-plan",
    f"{OPERATOR_PREFIX}/runtime-plan",
    f"{OPERATOR_PREFIX}/console-plan",
    f"{OPERATOR_PREFIX}/toolchain-plan",
    f"{OPERATOR_PREFIX}/tool-access-plan",
    f"{OPERATOR_PREFIX}/safety-plan",
    f"{OPERATOR_PREFIX}/goal-plan",
    f"{OPERATOR_PREFIX}/experience-plan",
    f"{OPERATOR_PREFIX}/notes-plan",
    f"{OPERATOR_PREFIX}/calendar-plan",
    f"{OPERATOR_PREFIX}/tasks-plan",
    f"{OPERATOR_PREFIX}/work-ops-plan",
    f"{OPERATOR_PREFIX}/change-brief",
    f"{OPERATOR_PREFIX}/backup-plan",
    f"{OPERATOR_PREFIX}/code-test-plan",
    f"{OPERATOR_PREFIX}/build-watch-plan",
    f"{OPERATOR_PREFIX}/document-search-plan",
    f"{OPERATOR_PREFIX}/research-plan",
    f"{OPERATOR_PREFIX}/gallery-plan",
    f"{OPERATOR_PREFIX}/file-ops-plan",
    f"{OPERATOR_PREFIX}/workspace-plan",
    f"{OPERATOR_PREFIX}/training-plan",
    f"{OPERATOR_PREFIX}/voice-plan",
    f"{OPERATOR_PREFIX}/autonomy-plan",
    f"{OPERATOR_PREFIX}/approval-plan",
    f"{OPERATOR_PREFIX}/loops-plan",
    f"{OPERATOR_PREFIX}/automation-plan",
    f"{OPERATOR_PREFIX}/memory-plan",
    f"{OPERATOR_PREFIX}/workday-plan",
    f"{OPERATOR_PREFIX}/model-ops-plan",
    f"{OPERATOR_PREFIX}/ai-runtime-plan",
    f"{OPERATOR_PREFIX}/models",
    f"{OPERATOR_PREFIX}/briefing",
    f"{OPERATOR_PREFIX}/activity-plan",
    f"{OPERATOR_PREFIX}/policy",
    f"{OPERATOR_PREFIX}/commands",
    f"{OPERATOR_PREFIX}/workflows",
    f"{OPERATOR_PREFIX}/routes",
    f"{OPERATOR_PREFIX}/route",
    f"{OPERATOR_PREFIX}/command-layer-plan",
]


FALSE_IF_PRESENT = [
    "executes",
    "routes_commands",
    "executes_commands",
    "approves_commands",
    "starts_workflows",
    "starts_jobs",
    "starts_models",
    "starts_training",
    "starts_services",
    "restarts_services",
    "repairs_services",
    "runs_tasks",
    "runs_search",
    "runs_shell",
    "runs_docker",
    "writes_files",
    "writes_activity",
    "changes_policy",
    "changes_settings",
    "reads_credentials",
    "reads_secrets",
    "writes_credentials",
    "creates_backup",
    "restores_data",
    "exports_data",
    "deletes_records",
    "deletes_files",
    "deletes_volumes",
    "uploads_files",
    "uses_network",
]


ROUTE_EXAMPLES = [
    ("Summarize today.", "summarize-today", False),
    ("Check containers and fix anything unhealthy.", "request-container-fix", True),
    ("Open my code workspace and run the tests.", "run-tests", False),
    ("Train a small model on this dataset.", "open-training-run-plan", False),
    ("Watch this repo until the build passes.", "request-build-watch-loop", True),
    ("Create a task from this note.", "draft-task-from-note", False),
    ("Search my local documents for this.", "search-local-documents", False),
    ("Explain what changed since yesterday.", "explain-changes-since-yesterday", False),
    ("Prepare a backup and verify it.", "prepare-backup", False),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _request(owner: str) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(current_user=owner),
        app=SimpleNamespace(
            state=SimpleNamespace(personal_docs_manager=None, rag_manager=None)
        ),
    )


def _endpoint_map() -> dict[str, Any]:
    from routes.operator_routes import setup_operator_routes

    routes = setup_operator_routes().routes
    endpoints: dict[str, Any] = {}
    for route in routes:
        if "GET" not in getattr(route, "methods", set()):
            continue
        endpoints[getattr(route, "path", "")] = route.endpoint
    return endpoints


def _kwargs_for(endpoint: Any, request: SimpleNamespace, limit: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for name, parameter in inspect.signature(endpoint).parameters.items():
        if name == "request":
            kwargs[name] = request
        elif name == "limit":
            kwargs[name] = limit
        elif name == "since":
            kwargs[name] = "yesterday"
        elif name == "note_id":
            kwargs[name] = ""
        elif name == "text":
            kwargs[name] = "summarize today"
        elif parameter.default is not inspect._empty:
            continue
        else:
            raise TypeError(f"unsupported required parameter {name!r}")
    return kwargs


def _call_endpoint(endpoint: Any, kwargs: dict[str, Any]) -> Any:
    value = endpoint(**kwargs)
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _summary_violations(summary: Any) -> list[str]:
    if not isinstance(summary, dict):
        return []
    violations: list[str] = []
    for key in FALSE_IF_PRESENT:
        if summary.get(key) is True:
            violations.append(key)
    return violations


def _check_payload(path: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "path": path,
            "status": "fail",
            "detail": f"payload is {type(payload).__name__}, expected dict",
        }
    if payload.get("ok") is not True:
        return {
            "path": path,
            "status": "fail",
            "detail": "payload ok flag is not true",
            "keys": sorted(payload.keys())[:20],
        }
    violations = _summary_violations(payload.get("summary"))
    if violations:
        return {
            "path": path,
            "status": "fail",
            "detail": "summary advertises active behavior",
            "violations": violations,
        }
    if path.endswith("-plan") and not payload.get("mode"):
        return {
            "path": path,
            "status": "fail",
            "detail": "plan route returned no mode",
            "keys": sorted(payload.keys())[:20],
        }
    return {
        "path": path,
        "status": "ok",
        "detail": payload.get("mode") or "read-only route loaded",
        "summary_keys": sorted((payload.get("summary") or {}).keys())[:20],
    }


def _check_route_example(endpoint: Any, request: SimpleNamespace, phrase: str, expected_id: str, approval_required: bool) -> dict[str, Any]:
    try:
        payload = _call_endpoint(endpoint, {"request": request, "text": phrase, "limit": 5})
    except Exception as exc:  # pragma: no cover - surfaced in smoke report.
        return {
            "phrase": phrase,
            "expected_id": expected_id,
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return {
            "phrase": phrase,
            "expected_id": expected_id,
            "status": "fail",
            "detail": "route payload did not return ok",
        }
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else None
    selected_id = str((selected or {}).get("id") or "")
    selected_approval = bool((selected or {}).get("approval_required"))
    if selected_id != expected_id:
        return {
            "phrase": phrase,
            "expected_id": expected_id,
            "selected_id": selected_id,
            "status": "fail",
            "detail": "target phrase selected the wrong route",
        }
    if selected_approval != approval_required:
        return {
            "phrase": phrase,
            "expected_id": expected_id,
            "selected_id": selected_id,
            "approval_required": selected_approval,
            "status": "fail",
            "detail": "target phrase approval requirement was wrong",
        }
    return {
        "phrase": phrase,
        "expected_id": expected_id,
        "selected_id": selected_id,
        "approval_required": selected_approval,
        "status": "ok",
        "detail": "target phrase routed correctly",
    }


def run_smoke(owner: str, limit: int) -> dict[str, Any]:
    endpoints = _endpoint_map()
    request = _request(owner)
    results: list[dict[str, Any]] = []
    for path in SMOKE_PATHS:
        endpoint = endpoints.get(path)
        if endpoint is None:
            results.append(
                {
                    "path": path,
                    "status": "fail",
                    "detail": "route endpoint not registered",
                }
            )
            continue
        try:
            kwargs = _kwargs_for(endpoint, request, limit)
            payload = _call_endpoint(endpoint, kwargs)
            results.append(_check_payload(path, payload))
        except Exception as exc:  # pragma: no cover - surfaced in smoke report.
            results.append(
                {
                    "path": path,
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
    route_endpoint = endpoints.get(f"{OPERATOR_PREFIX}/route")
    route_examples = [
        _check_route_example(route_endpoint, request, phrase, expected_id, approval_required)
        if route_endpoint is not None else {
            "phrase": phrase,
            "expected_id": expected_id,
            "status": "fail",
            "detail": "route endpoint not registered",
        }
        for phrase, expected_id, approval_required in ROUTE_EXAMPLES
    ]
    route_ok = sum(1 for result in results if result["status"] == "ok")
    route_fail = sum(1 for result in results if result["status"] == "fail")
    example_ok = sum(1 for result in route_examples if result["status"] == "ok")
    example_fail = sum(1 for result in route_examples if result["status"] == "fail")
    return {
        "generated_at": _utc_now(),
        "owner": owner,
        "route_count": len(results),
        "route_ok": route_ok,
        "route_fail": route_fail,
        "example_count": len(route_examples),
        "example_ok": example_ok,
        "example_fail": example_fail,
        "ok": route_ok + example_ok,
        "fail": route_fail + example_fail,
        "results": results,
        "route_examples": route_examples,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="smoke", help="Owner identity for route context")
    parser.add_argument("--limit", type=int, default=20, help="Activity row limit for plan routes")
    parser.add_argument("--report", default="", help="Optional JSON report path")
    args = parser.parse_args(argv)

    report = run_smoke(args.owner, max(1, min(200, args.limit)))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
