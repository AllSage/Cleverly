"""Shared auth helpers used by all route files."""

import os
from typing import Optional
from fastapi import Request, HTTPException


_PROXY_FWD_HEADERS = (
    "cf-connecting-ip",
    "cf-ray",
    "cf-visitor",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-real-ip",
    "forwarded",
)


def _header_value(headers, name: str):
    try:
        return headers.get(name) or headers.get(name.lower()) or headers.get(name.title())
    except Exception:
        return None


def _is_direct_loopback(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = (client.host if client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        return False
    headers = getattr(request, "headers", {}) or {}
    return not any(_header_value(headers, header) for header in _PROXY_FWD_HEADERS)


def get_current_user(request: Request) -> Optional[str]:
    """Get current username from request state (set by auth middleware)."""
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None) or getattr(request.state, "current_user", None)
    return getattr(request.state, 'current_user', None)


def require_api_scope(request: Request, scope: str) -> str:
    """Require a bearer API token with `scope` and return its owning user."""
    if not getattr(request.state, "api_token", False):
        raise HTTPException(403, "This endpoint requires an API token")
    scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    if scope not in scopes:
        raise HTTPException(403, f"API token is not scoped for {scope}")
    owner = getattr(request.state, "api_token_owner", None)
    if not owner:
        raise HTTPException(403, "API token is not bound to a user")
    return owner


def require_user(request: Request) -> str:
    """FastAPI dependency: reject unauthenticated callers, even if upstream
    middleware was bypassed (LOCALHOST_BYPASS, AUTH_ENABLED=false, SSRF from
    a sibling service). Returns the resolved username, or "" in unconfigured
    first-run mode when the caller is on loopback.

    Use this on routes that touch user data so middleware misconfig can't
    open them up.
    """
    u = get_current_user(request)
    if u:
        return u
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if os.getenv("AUTH_ENABLED", "true").lower() == "false" and _is_direct_loopback(request):
        return ""
    if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
        raise HTTPException(401, "Not authenticated")
    # Unconfigured / first-run mode: only allow loopback callers.
    if _is_direct_loopback(request):
        return ""
    raise HTTPException(401, "Not authenticated")


def require_privilege(request: Request, key: str) -> str:
    """Reject callers whose `auth.json` privilege flag for `key` is False.
    Returns the username so the route handler can keep using it.

    Admins always have every privilege via `auth_manager.get_privileges`
    (which returns ADMIN_PRIVILEGES wholesale), so this is a no-op for
    them. In unauthenticated single-user mode (`require_user` returns ""),
    privileges aren't enforced.
    """
    user = require_user(request)
    if not user:
        return user
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return user
    try:
        privs = auth_mgr.get_privileges(user) or {}
    except Exception:
        return user
    # True = permitted; missing key defaults to permitted (unknown privileges
    # fail open — the UI gates display-side).
    if not privs.get(key, True):
        raise HTTPException(403, f"Your account is not allowed to {key.replace('_', ' ')}.")
    return user


def owner_filter(query, model_cls, user: str, *, include_shared: bool = True):
    """Filter `query` so only rows owned by `user` (and optionally null-owner
    'shared' rows) come through. No-op when `user` is empty (single-user
    mode). Returns the modified query."""
    if not user:
        return query
    if include_shared:
        return query.filter((model_cls.owner == user) | (model_cls.owner == None))  # noqa: E711
    return query.filter(model_cls.owner == user)
