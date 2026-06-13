"""Sealed local code workspaces.

Code workspaces live under DATA_DIR/code-workspaces so Docker sealed mode keeps
repo contents inside the Cleverly data volume. Operations here deliberately
avoid network fetches and reject path traversal before touching disk.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from src.constants import DATA_DIR


MAX_FILE_BYTES = 1_000_000
MAX_READ_BYTES = 200_000
MAX_PATCH_BYTES = 2_000_000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_COMMAND_SECONDS = 300

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
}

DENIED_COMMAND_RE = re.compile(
    r"(^|[;&|]\s*)(curl|wget|ssh|scp|sftp|ftp|nc|ncat|telnet|docker|podman|kubectl)\b"
    r"|\bgit\s+(clone|pull|push|fetch|submodule|remote)\b"
    r"|\b(pip|python\s+-m\s+pip)\s+install\b"
    r"|\b(npm|pnpm|yarn)\s+(install|add|audit|publish)\b"
    r"|\b(rm\s+-rf\s+/|del\s+/s|format\b)\b",
    re.IGNORECASE,
)
DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:[/\\]")


class CodeWorkspaceError(ValueError):
    """User-correctable workspace error."""


def workspace_root(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else Path(os.getenv("CODE_WORKSPACE_DIR") or DATA_DIR) / "code-workspaces"
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def _index_path(root: Path) -> Path:
    return root / "workspaces.json"


def _load_index(root: Path) -> dict[str, dict[str, Any]]:
    path = _index_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(root: Path, data: dict[str, dict[str, Any]]) -> None:
    tmp = _index_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_index_path(root))


def _slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "workspace").strip()).strip(".-")
    return (slug or "workspace")[:60]


def _workspace_dir(root: Path, workspace_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", workspace_id or ""):
        raise CodeWorkspaceError("Invalid workspace id")
    path = (root / workspace_id).resolve()
    if root not in path.parents and path != root:
        raise CodeWorkspaceError("Workspace path escaped root")
    return path


def _require_workspace(workspace_id: str, *, owner: str = "", root: str | Path | None = None) -> tuple[Path, dict[str, Any]]:
    base = workspace_root(root)
    index = _load_index(base)
    meta = index.get(workspace_id)
    if not meta:
        raise CodeWorkspaceError("Workspace not found")
    if owner and meta.get("owner") and meta.get("owner") != owner:
        raise CodeWorkspaceError("Workspace belongs to another user")
    path = _workspace_dir(base, workspace_id)
    if not path.exists():
        raise CodeWorkspaceError("Workspace directory is missing")
    return path, meta


def _safe_path(workspace: Path, rel_path: str = "") -> Path:
    raw = (rel_path or "").strip()
    if "\x00" in raw or _is_absolute_like(raw):
        raise CodeWorkspaceError("Invalid path")
    rel = raw.replace("\\", "/")
    parts = _path_parts(rel)
    if ".." in parts:
        raise CodeWorkspaceError("Path escaped workspace")
    if ".git" in parts:
        raise CodeWorkspaceError(".git internals are not editable through workspace file APIs")
    target = (workspace / rel).resolve()
    if target != workspace and workspace not in target.parents:
        raise CodeWorkspaceError("Path escaped workspace")
    return target


def _path_parts(raw_path: str) -> list[str]:
    return [part for part in (raw_path or "").replace("\\", "/").split("/") if part and part != "."]


def _is_absolute_like(raw_path: str) -> bool:
    value = (raw_path or "").strip()
    return value.startswith(("/", "\\")) or bool(DRIVE_PREFIX_RE.match(value))


def _run(cmd: list[str], cwd: Path, *, input_text: str | None = None, timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=_offline_env(),
        )
        return {
            "stdout": _truncate(proc.stdout),
            "stderr": _truncate(proc.stderr),
            "exit_code": proc.returncode,
        }
    except FileNotFoundError as exc:
        return {"stdout": "", "stderr": str(exc), "exit_code": 127}
    except subprocess.TimeoutExpired as exc:
        return {
            "stdout": _truncate(exc.stdout or ""),
            "stderr": _truncate((exc.stderr or "") + f"\nTimed out after {timeout}s"),
            "exit_code": 124,
        }


def _truncate(text: str, limit: int = 20_000) -> str:
    if len(text or "") > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text or ""


def _offline_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "CLEVERLY_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PIP_NO_INDEX": "1",
        "npm_config_offline": "true",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
    })
    return env


def _shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-lc", command]


def _init_git(path: Path) -> None:
    if (path / ".git").exists():
        return
    _run(["git", "init"], path, timeout=20)
    _run(["git", "config", "user.name", "Cleverly"], path, timeout=10)
    _run(["git", "config", "user.email", "cleverly@local"], path, timeout=10)


def list_workspaces(*, owner: str = "", root: str | Path | None = None) -> list[dict[str, Any]]:
    base = workspace_root(root)
    items = []
    for meta in _load_index(base).values():
        if owner and meta.get("owner") and meta.get("owner") != owner:
            continue
        items.append(dict(meta))
    return sorted(items, key=lambda x: x.get("updated_at", 0), reverse=True)


def create_workspace(name: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    base = workspace_root(root)
    index = _load_index(base)
    workspace_id = f"{_slug(name)}-{uuid.uuid4().hex[:8]}"
    path = _workspace_dir(base, workspace_id)
    path.mkdir(parents=True, exist_ok=False)
    _init_git(path)
    now = time.time()
    meta = {
        "id": workspace_id,
        "name": (name or "Workspace").strip()[:80],
        "owner": owner or "",
        "path": str(path),
        "created_at": now,
        "updated_at": now,
    }
    index[workspace_id] = meta
    _save_index(base, index)
    return dict(meta)


def _archive_member_path(base: Path, member_name: str) -> Path:
    raw = (member_name or "").strip()
    name = raw.replace("\\", "/")
    parts = _path_parts(name)
    if not parts or "\x00" in raw or _is_absolute_like(raw):
        raise CodeWorkspaceError("Archive contains an invalid path")
    if ".." in parts:
        raise CodeWorkspaceError("Archive contains a path traversal entry")
    if ".git" in parts:
        raise CodeWorkspaceError("Archive .git directories are not allowed")
    target = (base / name).resolve()
    if target != base and base not in target.parents:
        raise CodeWorkspaceError("Archive contains a path traversal entry")
    return target


def _add_extracted_bytes(total: int, amount: int) -> int:
    total += max(0, int(amount or 0))
    if total > MAX_ARCHIVE_BYTES:
        raise CodeWorkspaceError("Archive expands past the allowed size limit")
    return total


def _copy_extracted(src: Path, dest: Path) -> None:
    children = [p for p in src.iterdir() if p.name not in {".DS_Store", "__MACOSX"}]
    if len(children) == 1 and children[0].is_dir():
        src = children[0]
    for item in src.iterdir():
        if item.name == "__MACOSX":
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def import_archive(name: str, archive_path: str | Path, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    archive = Path(archive_path)
    if not archive.exists() or not archive.is_file():
        raise CodeWorkspaceError("Archive not found")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise CodeWorkspaceError("Archive is too large")

    meta = create_workspace(name or archive.stem, owner=owner, root=root)
    workspace, _ = _require_workspace(meta["id"], owner=owner, root=root)
    try:
        with tempfile.TemporaryDirectory(dir=str(workspace_root(root))) as tmp_name:
            tmp = Path(tmp_name)
            lower = archive.name.lower()
            extracted_bytes = 0
            if lower.endswith(".zip"):
                with zipfile.ZipFile(archive) as zf:
                    for info in zf.infolist():
                        mode = (info.external_attr >> 16) & 0o170000
                        if mode == 0o120000:
                            raise CodeWorkspaceError("Archive symlinks are not allowed")
                        target = _archive_member_path(tmp, info.filename)
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            extracted_bytes = _add_extracted_bytes(extracted_bytes, info.file_size)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(info) as src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst)
            elif lower.endswith((".tar", ".tar.gz", ".tgz")):
                mode = "r:gz" if lower.endswith((".tar.gz", ".tgz")) else "r:"
                with tarfile.open(archive, mode) as tf:
                    for member in tf.getmembers():
                        if member.issym() or member.islnk() or not (member.isdir() or member.isreg()):
                            raise CodeWorkspaceError("Archive contains unsupported link or special file")
                        target = _archive_member_path(tmp, member.name)
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            extracted_bytes = _add_extracted_bytes(extracted_bytes, member.size)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            src = tf.extractfile(member)
                            if src is None:
                                continue
                            with src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst)
            else:
                raise CodeWorkspaceError("Use a .zip, .tar, .tar.gz, or .tgz archive")
            _copy_extracted(tmp, workspace)
        _init_git(workspace)
        _touch_workspace(meta["id"], owner=owner, root=root)
        return get_workspace(meta["id"], owner=owner, root=root)
    except Exception:
        delete_workspace(meta["id"], owner=owner, root=root)
        raise


def _touch_workspace(workspace_id: str, *, owner: str = "", root: str | Path | None = None) -> None:
    base = workspace_root(root)
    index = _load_index(base)
    meta = index.get(workspace_id)
    if not meta:
        return
    if owner and meta.get("owner") and meta.get("owner") != owner:
        raise CodeWorkspaceError("Workspace belongs to another user")
    meta["updated_at"] = time.time()
    _save_index(base, index)


def get_workspace(workspace_id: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    _, meta = _require_workspace(workspace_id, owner=owner, root=root)
    return dict(meta)


def delete_workspace(workspace_id: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    base = workspace_root(root)
    index = _load_index(base)
    meta = index.get(workspace_id)
    if not meta:
        return {"deleted": False}
    if owner and meta.get("owner") and meta.get("owner") != owner:
        raise CodeWorkspaceError("Workspace belongs to another user")
    path = _workspace_dir(base, workspace_id)
    if path.exists():
        shutil.rmtree(path)
    index.pop(workspace_id, None)
    _save_index(base, index)
    return {"deleted": True, "id": workspace_id}


def list_tree(workspace_id: str, rel_path: str = "", *, owner: str = "", root: str | Path | None = None, max_entries: int = 250) -> dict[str, Any]:
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    path = _safe_path(workspace, rel_path)
    if not path.exists() or not path.is_dir():
        raise CodeWorkspaceError("Directory not found")
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in SKIP_DIRS:
            continue
        if len(entries) >= max_entries:
            break
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": child.relative_to(workspace).as_posix(),
            "type": "dir" if child.is_dir() else "file",
            "size": stat.st_size,
        })
    return {"workspace_id": workspace_id, "path": rel_path or "", "entries": entries}


def read_file(workspace_id: str, rel_path: str, *, owner: str = "", root: str | Path | None = None, max_bytes: int = MAX_READ_BYTES) -> dict[str, Any]:
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    path = _safe_path(workspace, rel_path)
    if not path.exists() or not path.is_file():
        raise CodeWorkspaceError("File not found")
    if path.stat().st_size > max_bytes:
        raise CodeWorkspaceError(f"File exceeds {max_bytes} byte read limit")
    content = path.read_text(encoding="utf-8", errors="replace")
    return {"path": path.relative_to(workspace).as_posix(), "content": content, "size": len(content)}


def write_file(workspace_id: str, rel_path: str, content: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise CodeWorkspaceError(f"File exceeds {MAX_FILE_BYTES} byte write limit")
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    path = _safe_path(workspace, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _touch_workspace(workspace_id, owner=owner, root=root)
    return {"path": path.relative_to(workspace).as_posix(), "size": len(content), "exit_code": 0}


def apply_unified_diff(workspace_id: str, diff: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    if len(diff.encode("utf-8")) > MAX_PATCH_BYTES:
        raise CodeWorkspaceError("Patch is too large")
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    check = _run(["git", "apply", "--check", "--whitespace=nowarn"], workspace, input_text=diff, timeout=30)
    if check["exit_code"] != 0:
        return {"error": "Patch did not apply", **check}
    result = _run(["git", "apply", "--whitespace=nowarn"], workspace, input_text=diff, timeout=30)
    if result["exit_code"] == 0:
        _touch_workspace(workspace_id, owner=owner, root=root)
    return result


def run_command(workspace_id: str, command: str, *, owner: str = "", root: str | Path | None = None, timeout_seconds: int = 120) -> dict[str, Any]:
    command = (command or "").strip()
    if not command:
        raise CodeWorkspaceError("Command is required")
    if len(command) > 400:
        raise CodeWorkspaceError("Command is too long")
    if DENIED_COMMAND_RE.search(command):
        raise CodeWorkspaceError("Command is blocked in offline code workspace mode")
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    timeout = max(1, min(int(timeout_seconds or 120), MAX_COMMAND_SECONDS))
    result = _run(_shell_command(command), workspace, timeout=timeout)
    _touch_workspace(workspace_id, owner=owner, root=root)
    return result


def git_status(workspace_id: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    return _run(["git", "status", "--short"], workspace, timeout=20)


def git_diff(workspace_id: str, *, owner: str = "", root: str | Path | None = None, staged: bool = False) -> dict[str, Any]:
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    cmd = ["git", "diff", "--cached"] if staged else ["git", "diff"]
    return _run(cmd, workspace, timeout=30)


def git_commit(workspace_id: str, message: str, *, owner: str = "", root: str | Path | None = None) -> dict[str, Any]:
    workspace, _ = _require_workspace(workspace_id, owner=owner, root=root)
    message = (message or "Cleverly code workspace changes").strip()[:140]
    _run(["git", "add", "-A"], workspace, timeout=30)
    result = _run(["git", "commit", "-m", message], workspace, timeout=30)
    if result["exit_code"] == 0:
        _touch_workspace(workspace_id, owner=owner, root=root)
    return result


def summarize(items: Iterable[dict[str, Any]]) -> str:
    rows = []
    for item in items:
        rows.append(f"- {item.get('name')} (`{item.get('id')}`)")
    return "\n".join(rows) or "No code workspaces yet."
