"""Codex-like workflow for sealed Code Workspaces."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.database import ModelEndpoint, SessionLocal
from src import code_workspace
from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
from src.llm_core import llm_call_async
from src.offline_policy import is_local_model_url
from src.settings import get_setting, offline_mode


MAX_CONTEXT_CHARS = 80_000


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _model_match(wanted: str, model_id: str) -> bool:
    w = _slug(wanted)
    m = _slug(model_id)
    return bool(w and m and (w == m or w in m or m in w))


def resolve_model_key(model_key: str, owner: str = "") -> tuple[str, str, dict[str, str]]:
    key = (model_key or "").strip()
    if not key:
        raise code_workspace.CodeWorkspaceError("Set Code Workspace model key before running the coding agent")

    endpoint_hint = ""
    wanted_model = key
    if "@" in key:
        wanted_model, endpoint_hint = [part.strip() for part in key.rsplit("@", 1)]

    db = SessionLocal()
    try:
        endpoints = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()
        candidates: list[ModelEndpoint] = []
        for ep in endpoints:
            if endpoint_hint and endpoint_hint.lower() not in (ep.name or "").lower():
                continue
            ep_owner = getattr(ep, "owner", None)
            if owner and ep_owner and ep_owner != owner:
                continue
            if offline_mode() and not is_local_model_url(ep.base_url):
                continue
            candidates.append(ep)

        if not candidates:
            raise code_workspace.CodeWorkspaceError(
                "No enabled local model endpoint matches the Code Workspace model key"
            )

        for ep in candidates:
            cached = []
            try:
                cached = json.loads(ep.cached_models or "[]") or []
            except Exception:
                cached = []
            for model_id in cached:
                if _model_match(wanted_model, str(model_id)):
                    base = normalize_base(ep.base_url)
                    return build_chat_url(base), str(model_id), build_headers(ep.api_key, base)

        if endpoint_hint:
            ep = candidates[0]
            base = normalize_base(ep.base_url)
            return build_chat_url(base), wanted_model, build_headers(ep.api_key, base)
        if len(candidates) == 1:
            ep = candidates[0]
            base = normalize_base(ep.base_url)
            return build_chat_url(base), wanted_model, build_headers(ep.api_key, base)
        raise code_workspace.CodeWorkspaceError(
            "Model key did not match cached endpoint models. Use 'model@endpoint' or refresh model discovery."
        )
    finally:
        db.close()


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _extract_diff(text: str) -> str:
    raw = text or ""
    fence = re.search(r"```(?:diff|patch)?\s*(.*?)```", raw, re.S | re.I)
    if fence:
        raw = fence.group(1)
    start = raw.find("diff --git ")
    if start >= 0:
        return raw[start:].strip() + "\n"
    if raw.lstrip().startswith(("--- ", "*** Begin Patch")):
        return raw.strip() + "\n"
    return ""


def _repo_listing(workspace_id: str, owner: str, root=None) -> list[dict[str, Any]]:
    workspace, _ = code_workspace._require_workspace(workspace_id, owner=owner, root=root)
    rows = []
    for path, stat in code_workspace._iter_workspace_files(workspace):
        if len(rows) >= code_workspace.MAX_AGENT_TREE_ENTRIES:
            break
        rel = path.relative_to(workspace).as_posix()
        rows.append({"path": rel, "size": stat.st_size})
    return rows


def _read_context_files(workspace_id: str, paths: list[str], owner: str, root=None) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for rel in paths:
        rel = (rel or "").strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        if len(out) >= code_workspace.MAX_AGENT_FILES:
            break
        try:
            item = code_workspace.read_file(
                workspace_id,
                rel,
                owner=owner,
                root=root,
                max_bytes=code_workspace.MAX_AGENT_FILE_BYTES,
            )
        except code_workspace.CodeWorkspaceError:
            continue
        out.append(item)
    return out


def _fallback_paths(listing: list[dict[str, Any]], task: str) -> list[str]:
    priority_names = {
        "readme.md", "package.json", "pyproject.toml", "requirements.txt",
        "docker-compose.yml", "dockerfile", "app.py", "main.py",
    }
    task_words = {w for w in re.findall(r"[A-Za-z0-9_]{4,}", task.lower())}
    scored = []
    for row in listing:
        path = row["path"]
        name = Path(path).name.lower()
        suffix = Path(path).suffix.lower()
        if suffix and suffix not in code_workspace.TEXT_SUFFIXES:
            continue
        score = 0
        if name in priority_names:
            score += 5
        low = path.lower()
        score += sum(1 for w in task_words if w in low)
        scored.append((score, path))
    return [path for _score, path in sorted(scored, key=lambda x: (-x[0], x[1]))[: code_workspace.MAX_AGENT_FILES]]


async def _choose_files(url: str, model: str, headers: dict, task: str, listing: list[dict[str, Any]]) -> list[str]:
    listing_text = "\n".join(f"{row['path']} ({row['size']} bytes)" for row in listing)
    messages = [
        {
            "role": "system",
            "content": (
                "You select files for an offline coding agent. Return JSON only with keys "
                "paths (array of repo-relative files, max 12) and plan (short string)."
            ),
        },
        {"role": "user", "content": f"Task:\n{task}\n\nRepo files:\n{listing_text}"},
    ]
    response = await llm_call_async(url, model, messages, headers=headers, temperature=0.2, max_tokens=1200, timeout=120)
    data = _extract_json(response)
    paths = data.get("paths") if isinstance(data, dict) else []
    return [str(p) for p in paths if isinstance(p, str)][: code_workspace.MAX_AGENT_FILES]


def _build_patch_prompt(task: str, files: list[dict[str, Any]], status: str, prior: str = "") -> list[dict[str, str]]:
    chunks = []
    total = 0
    for item in files:
        header = f"\n--- FILE: {item['path']} ---\n"
        body = item.get("content") or ""
        part = header + body
        if total + len(part) > MAX_CONTEXT_CHARS:
            break
        chunks.append(part)
        total += len(part)
    prior_block = f"\nPrevious attempt/test output:\n{prior}\n" if prior else ""
    return [
        {
            "role": "system",
            "content": (
                "You are an offline coding agent. Produce a unified diff only. "
                "Do not include prose outside a diff fence. Do not suggest network installs. "
                "Prefer small, targeted changes."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Task:\n{task}\n\nGit status:\n{status or '(clean)'}\n"
                f"{prior_block}\nRepo context:{''.join(chunks)}\n\nReturn one unified diff."
            ),
        },
    ]


async def run_agent(
    workspace_id: str,
    task: str,
    *,
    owner: str = "",
    model_key: str = "",
    test_command: str = "",
    max_rounds: int = 2,
    selected_paths: list[str] | None = None,
    apply_changes: bool = False,
    root=None,
) -> dict[str, Any]:
    task = (task or "").strip()
    if not task:
        raise code_workspace.CodeWorkspaceError("Agent task is required")
    key = (model_key or get_setting("code_workspace_model_key", "") or "").strip()
    url, model, headers = resolve_model_key(key, owner=owner)

    snapshot = code_workspace.create_snapshot(workspace_id, "Before agent run", owner=owner, root=root)
    listing = _repo_listing(workspace_id, owner, root=root)
    paths = list(selected_paths or [])
    if not paths:
        try:
            paths = await _choose_files(url, model, headers, task, listing)
        except Exception:
            paths = []
    if not paths:
        paths = _fallback_paths(listing, task)
    files = _read_context_files(workspace_id, paths, owner, root=root)
    if not files:
        raise code_workspace.CodeWorkspaceError("No readable source files were selected for the coding agent")

    steps: list[dict[str, Any]] = [{"phase": "snapshot", "snapshot": snapshot}, {"phase": "context", "paths": [f["path"] for f in files]}]
    final_diff = ""
    test_result = None
    prior = ""
    max_rounds = max(1, min(int(max_rounds or 1), 3))

    for round_no in range(1, max_rounds + 1):
        status = code_workspace.git_status(workspace_id, owner=owner, root=root).get("stdout", "")
        response = await llm_call_async(
            url,
            model,
            _build_patch_prompt(task, files, status, prior),
            headers=headers,
            temperature=0.2,
            max_tokens=6000,
            timeout=180,
            max_retries=1,
        )
        diff = _extract_diff(response)
        if not diff:
            steps.append({"phase": "patch", "round": round_no, "error": "Model did not return a unified diff"})
            break
        if not apply_changes:
            final_diff = diff
            steps.append({"phase": "draft", "round": round_no, "exit_code": 0})
            break
        patch_result = code_workspace.apply_unified_diff(workspace_id, diff, owner=owner, root=root)
        steps.append({"phase": "patch", "round": round_no, "exit_code": patch_result.get("exit_code"), "stderr": patch_result.get("stderr", "")})
        if patch_result.get("exit_code") != 0:
            prior = patch_result.get("stderr") or patch_result.get("stdout") or "Patch failed"
            continue
        final_diff = diff
        if test_command:
            test_result = code_workspace.run_command(workspace_id, test_command, owner=owner, root=root, timeout_seconds=180)
            steps.append({"phase": "test", "round": round_no, **test_result})
            if test_result.get("exit_code") == 0:
                break
            prior = "\n".join([test_result.get("stdout", ""), test_result.get("stderr", "")])
            files = _read_context_files(workspace_id, [f["path"] for f in files], owner, root=root)
            continue
        break

    status = code_workspace.git_status(workspace_id, owner=owner, root=root)
    diff_result = code_workspace.git_diff(workspace_id, owner=owner, root=root)
    return {
        "ok": True,
        "model_key": key,
        "model": model,
        "snapshot": snapshot,
        "selected_paths": [f["path"] for f in files],
        "proposed_diff": final_diff if not apply_changes else "",
        "applied_diff": final_diff if apply_changes else "",
        "test_result": test_result,
        "status": status,
        "diff": diff_result,
        "steps": steps,
        "applied": bool(apply_changes and final_diff),
        "exit_code": 0 if (not test_result or test_result.get("exit_code") == 0) and final_diff else 1,
    }
