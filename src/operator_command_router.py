"""Read-only command route proof for the Cleverly operator console."""

from __future__ import annotations

import re
from typing import Any


TRUST_LEVELS = ("local", "approval", "network", "danger")
DEFAULT_TRUST_POLICY = {
    "local": "auto",
    "approval": "ask",
    "network": "ask",
    "danger": "ask",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "can",
    "could",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "please",
    "that",
    "the",
    "this",
    "to",
    "what",
    "why",
    "would",
    "you",
}


def _trim(value: Any, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def normalize_operator_query(value: Any) -> str:
    """Normalize user-facing command text without losing intent words."""
    original = _trim(value, 1000)
    text = original
    previous = ""
    while text and text != previous:
        previous = text
        text = re.sub(r"^[\s\"'`]+|[\s\"'`.!?]+$", "", text)
        text = re.sub(r"^(?:hey|ok(?:ay)?|yo|hi|hello)\s+cleverly[\s,:;-]*", "", text, flags=re.I)
        text = re.sub(r"^cleverly[\s,:;-]+", "", text, flags=re.I)
        text = re.sub(
            r"^(?:please|can\s+you|could\s+you|would\s+you|will\s+you|i\s+need\s+you\s+to|can\s+we|let'?s)\s+",
            "",
            text,
            flags=re.I,
        ).strip()
    return text or original


def _match_text(value: Any) -> str:
    text = normalize_operator_query(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> list[str]:
    tokens = []
    for token in re.split(r"[^a-z0-9]+", _match_text(value)):
        token = token.strip()
        if len(token) > 1 and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def _trust_level(command: dict[str, Any]) -> str:
    trust = str(command.get("trust") or "local").lower()
    return trust if trust in TRUST_LEVELS else "local"


def _trust_policy(policy: dict[str, Any] | None) -> dict[str, str]:
    normalized = dict(DEFAULT_TRUST_POLICY)
    if isinstance(policy, dict):
        for level in TRUST_LEVELS:
            mode = str(policy.get(level) or normalized[level]).lower()
            normalized[level] = mode if mode in {"auto", "ask"} else normalized[level]
    return normalized


def _trust_mode(command: dict[str, Any], policy: dict[str, Any] | None) -> str:
    if command.get("alwaysAsk") or command.get("always_ask"):
        return "ask"
    return _trust_policy(policy).get(_trust_level(command), "ask")


def _command_priority(command: dict[str, Any]) -> int:
    try:
        return max(-1000, min(1000, int(command.get("priority") or 0)))
    except (TypeError, ValueError):
        return 0


def _command_phrases(command: dict[str, Any]) -> list[str]:
    raw = [
        command.get("id"),
        command.get("title"),
        *(command.get("keywords") if isinstance(command.get("keywords"), list) else []),
    ]
    phrases: list[str] = []
    seen: set[str] = set()
    for item in raw:
        phrase = _match_text(item)
        if len(phrase) < 3 or phrase in seen:
            continue
        seen.add(phrase)
        phrases.append(phrase)
    return phrases


def _command_words(command: dict[str, Any]) -> set[str]:
    raw = [
        command.get("id"),
        command.get("title"),
        command.get("subtitle"),
        command.get("category"),
        *(command.get("keywords") if isinstance(command.get("keywords"), list) else []),
    ]
    return set(_tokens(" ".join(str(item or "") for item in raw)))


def _workflow_expected_id(workflow: dict[str, Any]) -> str:
    return _trim(
        workflow.get("expectedRouteId")
        or workflow.get("expected_route_id")
        or workflow.get("commandId")
        or workflow.get("command_id")
        or workflow.get("id"),
        160,
    )


def _workflow_command_ids(workflow: dict[str, Any]) -> set[str]:
    return {
        item
        for item in (
            workflow.get("id"),
            workflow.get("commandId"),
            workflow.get("command_id"),
            workflow.get("approvalId"),
            workflow.get("approval_id"),
            workflow.get("expectedRouteId"),
            workflow.get("expected_route_id"),
        )
        if _trim(item, 160)
    }


def _workflow_match_score(query: str, workflow: dict[str, Any]) -> tuple[int, str]:
    phrase = _match_text(workflow.get("phrase"))
    if not phrase or not query:
        return 0, ""
    if phrase == query:
        return 260, "workflow phrase exact match"
    if phrase in query or query in phrase:
        return 180, "workflow phrase contains query"
    phrase_tokens = set(_tokens(phrase))
    query_tokens = set(_tokens(query))
    if not phrase_tokens or not query_tokens:
        return 0, ""
    overlap = phrase_tokens & query_tokens
    if len(overlap) >= 3 and len(overlap) >= min(len(query_tokens), len(phrase_tokens)) // 2:
        return 80 + len(overlap) * 5, "workflow phrase token overlap"
    return 0, ""


def _workflow_evidence(query: str, workflows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {}
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        score, reason = _workflow_match_score(query, workflow)
        if not score:
            continue
        expected_id = _workflow_expected_id(workflow)
        record = {
            "id": _trim(workflow.get("id") or expected_id, 160),
            "phrase": _trim(workflow.get("phrase"), 300),
            "title": _trim(workflow.get("title") or workflow.get("plan") or expected_id, 240),
            "area": _trim(workflow.get("area") or "Workflow", 120),
            "state": _trim(workflow.get("state") or "warn", 80),
            "expected_route_id": expected_id,
            "approval_id": _trim(workflow.get("approvalId") or workflow.get("approval_id"), 160),
            "proof": _trim(workflow.get("proof") or workflow.get("detail"), 500),
            "score": score,
            "reason": reason,
        }
        for command_id in _workflow_command_ids(workflow):
            command_record = dict(record)
            if expected_id and command_id == expected_id:
                command_record["score"] = score + 60
                command_record["reason"] = f"{reason}; expected route"
            evidence.setdefault(command_id, []).append(command_record)
    return evidence


def _score_command(command: dict[str, Any], query: str, evidence: list[dict[str, Any]]) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    if evidence:
        best = max(int(item.get("score") or 0) for item in evidence)
        score += best
        reasons.append(evidence[0].get("reason") or "workflow evidence")

    phrases = _command_phrases(command)
    for phrase in phrases:
        if phrase == query:
            score += 150
            reasons.append("command phrase exact match")
            break
        if phrase and (phrase in query or query in phrase):
            score += 85
            reasons.append("command phrase contains query")
            break

    query_tokens = _tokens(query)
    words = _command_words(command)
    exact = [
        token for token in query_tokens
        if token in words or (token.endswith("s") and token[:-1] in words)
    ]
    if exact:
        score += len(exact) * 8
        reasons.append(f"{len(exact)} command token match{'es' if len(exact) != 1 else ''}")

    if score:
        score += min(max(_command_priority(command), 0), 20)
    return score, reasons


def _match_record(
    command: dict[str, Any],
    *,
    score: int,
    reasons: list[str],
    policy: dict[str, Any] | None,
    workflow_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    trust = _trust_level(command)
    mode = _trust_mode(command, policy)
    return {
        "id": _trim(command.get("id"), 160),
        "title": _trim(command.get("title") or command.get("id"), 240),
        "subtitle": _trim(command.get("subtitle"), 500),
        "category": _trim(command.get("category") or "Operator", 120),
        "trust": trust,
        "trust_mode": mode,
        "approval_required": mode == "ask",
        "workflow": bool(command.get("workflow")),
        "score": score,
        "reasons": reasons[:5],
        "workflow_evidence": workflow_evidence[:5],
    }


def resolve_operator_route(
    text: Any,
    commands: list[dict[str, Any]],
    workflows: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Resolve text against the persisted command/workflow catalog without executing it."""
    query = _trim(text, 1000)
    normalized = _match_text(query)
    workflows = workflows or []
    workflow_by_command = _workflow_evidence(normalized, workflows)
    matches: list[dict[str, Any]] = []
    for command in commands if isinstance(commands, list) else []:
        if not isinstance(command, dict) or not _trim(command.get("id"), 160):
            continue
        evidence = workflow_by_command.get(_trim(command.get("id"), 160), [])
        score, reasons = _score_command(command, normalized, evidence)
        if score <= 0:
            continue
        matches.append(_match_record(
            command,
            score=score,
            reasons=reasons,
            policy=policy,
            workflow_evidence=evidence,
        ))
    matches.sort(key=lambda item: (-int(item["score"]), item["title"].lower()))
    limit = max(1, min(20, int(limit or 5)))
    selected = matches[0] if matches and int(matches[0]["score"]) >= 12 else None
    return {
        "mode": "read-only-local-route",
        "query": query,
        "normalized_query": normalized,
        "configured": bool(commands),
        "selected": selected,
        "matches": matches[:limit],
        "fallback": None if selected else {
            "id": "chat-command",
            "title": "Send To Cleverly",
            "trust": "local",
            "trust_mode": "auto",
            "approval_required": False,
        },
        "summary": {
            "commands": len(commands) if isinstance(commands, list) else 0,
            "workflows": len(workflows),
            "matched": bool(selected),
            "approval_required": bool(selected and selected.get("approval_required")),
        },
    }


def resolve_operator_route_matrix(
    commands: list[dict[str, Any]],
    workflows: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve every persisted workflow phrase and summarize route readiness."""
    command_by_id = {
        _trim(command.get("id"), 160): command
        for command in commands if isinstance(command, dict) and _trim(command.get("id"), 160)
    }
    rows: list[dict[str, Any]] = []
    for workflow in workflows if isinstance(workflows, list) else []:
        if not isinstance(workflow, dict) or not _trim(workflow.get("phrase"), 300):
            continue
        result = resolve_operator_route(workflow.get("phrase"), commands, workflows, policy, limit=3)
        selected = result.get("selected") if isinstance(result.get("selected"), dict) else None
        expected_id = _workflow_expected_id(workflow)
        command_id = _trim(workflow.get("commandId") or workflow.get("command_id"), 160)
        approval_id = _trim(workflow.get("approvalId") or workflow.get("approval_id"), 160)
        command = command_by_id.get(command_id)
        approval = command_by_id.get(approval_id) if approval_id else None
        command_mode = _trust_mode(command, policy) if command else "missing"
        approval_mode = _trust_mode(approval, policy) if approval else ""
        route_ready = bool(selected and selected.get("id") == expected_id)
        approval_ready = not approval_id or (approval is not None and approval_mode == "ask")
        command_ready = command is not None
        state = "ok" if route_ready and command_ready and approval_ready else ("warn" if selected else "error")
        rows.append({
            "id": _trim(workflow.get("id") or expected_id, 160),
            "phrase": _trim(workflow.get("phrase"), 300),
            "title": _trim(workflow.get("title") or workflow.get("plan") or expected_id, 240),
            "area": _trim(workflow.get("area") or "Workflow", 120),
            "command_id": command_id,
            "approval_id": approval_id,
            "expected_route_id": expected_id,
            "selected_id": selected.get("id") if selected else "",
            "route_ready": route_ready,
            "command_ready": command_ready,
            "approval_ready": approval_ready,
            "command_mode": command_mode,
            "approval_mode": approval_mode,
            "state": state,
            "proof": _trim(workflow.get("proof") or workflow.get("detail"), 500),
            "matches": result.get("matches") or [],
        })
    total = len(rows)
    route_ready_count = sum(1 for row in rows if row["route_ready"])
    ready_count = sum(1 for row in rows if row["state"] == "ok")
    approval_gated_count = sum(1 for row in rows if row["approval_id"])
    return {
        "mode": "read-only-local-route-matrix",
        "configured": bool(commands),
        "rows": rows,
        "summary": {
            "total": total,
            "ready": ready_count,
            "route_ready": route_ready_count,
            "unresolved": total - route_ready_count,
            "approval_gated": approval_gated_count,
            "approval_ready": sum(1 for row in rows if row["approval_id"] and row["approval_ready"]),
        },
    }
