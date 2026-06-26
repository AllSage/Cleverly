"""Read-only gallery, upload, and image-media readiness plan."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.constants import DATA_DIR, UPLOAD_DIR
from src.settings import DEFAULT_FEATURES, load_features, load_settings, offline_mode


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _trim(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _feature(features: dict[str, Any], key: str, default: bool = True) -> bool:
    return bool(features.get(key, DEFAULT_FEATURES.get(key, default)))


def _path_count(path: Path, extensions: set[str] | None = None) -> tuple[int, int, bool]:
    try:
        if not path.exists():
            return 0, 0, False
        total = 0
        size = 0
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            if extensions and item.suffix.lower() not in extensions:
                continue
            total += 1
            try:
                size += item.stat().st_size
            except OSError:
                pass
        return total, size, True
    except OSError:
        return 0, 0, False


def _media_rows(data_root: Path, upload_root: Path) -> list[dict[str, Any]]:
    generated_root = data_root / "generated_images"
    gallery_root = data_root / "gallery"
    gallery_uploads_root = data_root / "gallery_uploads"
    rows = []
    for row_id, title, path, extensions in (
        ("generated-images", "Generated image files", generated_root, IMAGE_EXTENSIONS),
        ("gallery-files", "Gallery media files", gallery_root, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS),
        ("gallery-uploads", "Gallery upload files", gallery_uploads_root, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS),
        ("chat-uploads", "Chat upload files", upload_root, None),
    ):
        count, size, exists = _path_count(path, extensions)
        rows.append({
            "id": row_id,
            "state": "ok" if exists and count else ("warn" if exists else "error"),
            "badge": "media",
            "title": title,
            "detail": f"{count} file{'s' if count != 1 else ''}; {size} bytes; path {'exists' if exists else 'missing'}",
            "path": str(path),
            "count": count,
            "size": size,
            "exists": exists,
        })
    return rows


def _api_action(
    path: str,
    title: str,
    *,
    method: str = "GET",
    writes: bool = False,
    deletes: bool = False,
    uses_network: bool = False,
    requires_approval: bool = False,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "title": title,
        "writes": writes,
        "deletes": deletes,
        "executes": False,
        "uses_network": uses_network,
        "requires_approval": requires_approval,
    }


def _gallery_alert_rows(
    *,
    media_rows: list[dict[str, Any]],
    features: dict[str, Any],
    settings: dict[str, Any],
    offline: bool,
    upload_stats: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in media_rows:
        if row.get("exists"):
            continue
        rows.append({
            "id": f"gallery-root-missing-{row.get('id')}",
            "state": "error",
            "badge": "path",
            "title": f"Media root missing: {row.get('title')}",
            "detail": f"{row.get('path')} is not present in local data storage.",
            "action": "open-local-data-map",
            "actionLabel": "Data",
            "requires_approval": False,
            "uses_network": False,
        })
    if not _feature(features, "gallery", True):
        rows.append({
            "id": "gallery-feature-disabled",
            "state": "warn",
            "badge": "flag",
            "title": "Gallery feature disabled",
            "detail": "Gallery UI and media library routes are disabled by feature policy.",
            "action": "open-offline",
            "actionLabel": "Policy",
            "requires_approval": False,
            "uses_network": False,
        })
    if not bool(settings.get("image_gen_enabled", True)):
        rows.append({
            "id": "gallery-image-generation-disabled",
            "state": "warn",
            "badge": "img",
            "title": "Image generation disabled",
            "detail": "Generated-image creation is disabled; Gallery remains available for local media review.",
            "action": "open-model-preflight",
            "actionLabel": "Models",
            "requires_approval": False,
            "uses_network": False,
        })
    if not _trim(settings.get("image_model")):
        rows.append({
            "id": "gallery-image-model-missing",
            "state": "warn",
            "badge": "model",
            "title": "Image model route missing",
            "detail": "No image model is configured for generated-image workflows.",
            "action": "open-model-preflight",
            "actionLabel": "Models",
            "requires_approval": False,
            "uses_network": False,
        })
    if not bool(settings.get("vision_enabled", True)):
        rows.append({
            "id": "gallery-vision-disabled",
            "state": "warn",
            "badge": "vision",
            "title": "Vision captioning disabled",
            "detail": "Uploaded image OCR/caption cache refreshes are disabled.",
            "action": "open-model-preflight",
            "actionLabel": "Models",
            "requires_approval": False,
            "uses_network": False,
        })
    if not offline and _feature(features, "external_model_endpoints", True):
        rows.append({
            "id": "gallery-external-image-endpoints-enabled",
            "state": "warn",
            "badge": "net",
            "title": "External image endpoints enabled",
            "detail": "Image and vision model routes may use external endpoints when selected.",
            "action": "open-offline",
            "actionLabel": "Offline",
            "requires_approval": True,
            "uses_network": True,
        })
    if isinstance(upload_stats, dict):
        total_uploads = int(upload_stats.get("files") or upload_stats.get("total") or upload_stats.get("count") or 0)
        if total_uploads == 0:
            rows.append({
                "id": "gallery-upload-index-empty",
                "state": "warn",
                "badge": "upload",
                "title": "Upload index empty",
                "detail": "Upload stats are reachable but no uploaded files are reported.",
                "action": "open-gallery",
                "actionLabel": "Gallery",
                "requires_approval": False,
                "uses_network": False,
            })
    rows.append({
        "id": "gallery-write-gates",
        "state": "warn",
        "badge": "ask",
        "title": "Media write/delete gates require review",
        "detail": "Uploads, edits, replacements, AI tagging, zip export, and deletion stay behind explicit UI/API actions.",
        "action": "open-backup-preflight",
        "actionLabel": "Backup",
        "requires_approval": True,
        "uses_network": False,
    })
    return rows[:16]


def _entry_rows(
    *,
    gallery_enabled: bool,
    media_file_count: int,
    image_generation_enabled: bool,
    vision_enabled: bool,
) -> list[dict[str, Any]]:
    feature_state = "ok" if gallery_enabled else "warn"
    workflow_state = "ok" if gallery_enabled and media_file_count > 0 else "warn"
    generation_detail = (
        "Image generation and vision routes are configured for review."
        if image_generation_enabled and vision_enabled
        else "Image generation or vision routes need model/policy review before media automation."
    )
    common = {
        "command_id": "open-library-preflight",
        "start_command_id": "open-gallery",
        "approval_api": "/api/gallery/upload",
        "requires_approval": True,
        "executes": False,
        "uploads_files": False,
        "generates_images": False,
        "edits_media": False,
        "deletes_media": False,
        "exports_media": False,
        "refreshes_vision": False,
        "uses_network": False,
    }
    return [
        {
            **common,
            "id": "gallery-dashboard-route",
            "entry": "dashboard",
            "state": feature_state,
            "badge": "dash",
            "title": "Dashboard media preflight",
            "detail": "The Library/Gallery card opens read-only media readiness before any upload, generation, edit, export, or deletion.",
            "action": "open-library-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "gallery-text-route",
            "entry": "text",
            "state": feature_state,
            "badge": "text",
            "title": "Typed media request route",
            "detail": "Typed gallery and media requests route to Library Operations Preflight before opening Gallery or write-capable media APIs.",
            "action": "open-library-preflight",
            "actionLabel": "Preflight",
        },
        {
            **common,
            "id": "gallery-palette-route",
            "entry": "palette",
            "state": feature_state,
            "badge": "cmd",
            "title": "Palette media route",
            "detail": "The command palette separates read-only Gallery review from upload, replacement, export, deletion, and vision refresh APIs.",
            "action": "open-command-palette",
            "actionLabel": "Palette",
        },
        {
            **common,
            "id": "gallery-voice-route",
            "entry": "voice",
            "state": feature_state,
            "badge": "voice",
            "title": "Voice media route",
            "detail": "Voice transcripts use the same local route and open media preflight before any write-capable Gallery action.",
            "action": "start-voice-command",
            "actionLabel": "Voice",
        },
        {
            **common,
            "id": "gallery-workflow-route",
            "entry": "workflow",
            "state": workflow_state,
            "badge": "flow",
            "title": "Workflow media handoff",
            "detail": f"Workflow handoff can review local media paths and model gates; {generation_detail}",
            "action": "open-automation-map",
            "actionLabel": "Workflow",
        },
    ]


def _handoff_row(
    row_id: str,
    title: str,
    detail: str,
    *,
    target_api: str,
    method: str,
    state: str = "warn",
    badge: str = "handoff",
    action: str = "open-library-preflight",
    action_label: str = "Review",
    requires_approval: bool = True,
    uploads_files: bool = False,
    generates_images: bool = False,
    edits_media: bool = False,
    deletes_media: bool = False,
    exports_media: bool = False,
    refreshes_vision: bool = False,
    uses_network: bool = False,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "state": state,
        "badge": badge,
        "title": title,
        "detail": detail,
        "target_api": target_api,
        "method": method,
        "approval_command_id": "open-library-preflight",
        "action": action,
        "actionLabel": action_label,
        "requires_approval": requires_approval,
        "executes": False,
        "uploads_files": False,
        "generates_images": False,
        "edits_media": False,
        "deletes_media": False,
        "exports_media": False,
        "refreshes_vision": False,
        "uses_network": uses_network,
        "gated_operation": {
            "uploads_files": uploads_files,
            "generates_images": generates_images,
            "edits_media": edits_media,
            "deletes_media": deletes_media,
            "exports_media": exports_media,
            "refreshes_vision": refreshes_vision,
            "uses_network": uses_network,
        },
    }


def _handoff_rows(
    *,
    gallery_enabled: bool,
    media_file_count: int,
    image_generation_enabled: bool,
    vision_enabled: bool,
    offline: bool,
) -> list[dict[str, Any]]:
    enabled_state = "ok" if gallery_enabled else "error"
    media_state = "ok" if media_file_count else "loading"
    generation_state = "ok" if image_generation_enabled else "error"
    vision_state = "ok" if vision_enabled else "error"
    network_state = "ok" if offline else "warn"
    return [
        _handoff_row(
            "gallery-upload-import-handoff",
            "Upload/import media handoff",
            "Uploads and imports must move from read-only Library Preflight to the approved Gallery upload API.",
            target_api="/api/gallery/upload",
            method="POST",
            state=enabled_state,
            badge="upload",
            action="open-gallery",
            action_label="Gallery",
            uploads_files=True,
        ),
        _handoff_row(
            "gallery-generate-image-handoff",
            "AI media generation handoff",
            "AI upscale and style-transfer requests require model review before leaving the preflight surface.",
            target_api="/api/gallery/style-transfer",
            method="POST",
            state=generation_state,
            badge="gen",
            action="open-model-preflight",
            action_label="Models",
            generates_images=True,
        ),
        _handoff_row(
            "gallery-edit-transform-handoff",
            "Edit/transform media handoff",
            "Replacement and transform flows stay gated behind explicit Gallery review.",
            target_api="/api/gallery/{image_id}/replace",
            method="POST",
            state=media_state,
            badge="edit",
            action="open-gallery",
            action_label="Gallery",
            edits_media=True,
        ),
        _handoff_row(
            "gallery-delete-archive-handoff",
            "Delete/archive media handoff",
            "Delete-capable media operations require a destructive-action approval path.",
            target_api="/api/gallery/{image_id}",
            method="DELETE",
            state=media_state,
            badge="delete",
            action="open-backup-preflight",
            action_label="Backup",
            deletes_media=True,
        ),
        _handoff_row(
            "gallery-export-download-handoff",
            "Export/download media handoff",
            "Zip export and download preparation remain visible as file-producing local actions.",
            target_api="/api/gallery/download-zip",
            method="POST",
            state=media_state,
            badge="export",
            action="open-backup-preflight",
            action_label="Backup",
            exports_media=True,
        ),
        _handoff_row(
            "gallery-vision-refresh-handoff",
            "Vision refresh handoff",
            "OCR/caption cache refreshes require an explicit local vision route and model-policy review.",
            target_api="/api/upload/{file_id}/vision",
            method="GET",
            state=vision_state,
            badge="vision",
            action="open-model-preflight",
            action_label="Models",
            refreshes_vision=True,
        ),
        _handoff_row(
            "gallery-network-provider-handoff",
            "Network provider handoff",
            "External image or vision providers stay blocked by offline posture until deliberately enabled.",
            target_api="/api/operator/offline-status",
            method="GET",
            state=network_state,
            badge="net",
            action="open-offline",
            action_label="Offline",
            requires_approval=not offline,
            uses_network=not offline,
        ),
    ]


def run_operator_gallery_plan(
    owner: str = "local",
    *,
    features: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    upload_stats: dict[str, Any] | None = None,
    data_root: str | Path | None = None,
    upload_root: str | Path | None = None,
    offline: bool | None = None,
) -> dict[str, Any]:
    """Return read-only local media, gallery, upload, and image workflow evidence."""
    owner = owner or "local"
    try:
        loaded_features = load_features()
    except Exception:
        loaded_features = {}
    try:
        loaded_settings = load_settings()
    except Exception:
        loaded_settings = {}
    features = {**loaded_features, **(features or {})}
    settings = {**loaded_settings, **(settings or {})}
    data_path = Path(data_root or DATA_DIR)
    upload_path = Path(upload_root or UPLOAD_DIR)
    offline_state = offline_mode() if offline is None else bool(offline)
    media_rows = _media_rows(data_path, upload_path)
    alert_rows = _gallery_alert_rows(
        media_rows=media_rows,
        features=features,
        settings=settings,
        offline=offline_state,
        upload_stats=upload_stats,
    )
    total_files = sum(int(row.get("count") or 0) for row in media_rows)
    total_size = sum(int(row.get("size") or 0) for row in media_rows)
    gallery_enabled = _feature(features, "gallery", True)
    image_generation_enabled = bool(settings.get("image_gen_enabled", True))
    vision_enabled = bool(settings.get("vision_enabled", True))
    entry_rows = _entry_rows(
        gallery_enabled=gallery_enabled,
        media_file_count=total_files,
        image_generation_enabled=image_generation_enabled,
        vision_enabled=vision_enabled,
    )
    handoff_rows = _handoff_rows(
        gallery_enabled=gallery_enabled,
        media_file_count=total_files,
        image_generation_enabled=image_generation_enabled,
        vision_enabled=vision_enabled,
        offline=offline_state,
    )
    api_actions = [
        _api_action("/api/operator/gallery-plan", "Read gallery media plan"),
        _api_action("/api/gallery/stats", "Read gallery stats"),
        _api_action("/api/gallery/library", "Read gallery library"),
        _api_action("/api/upload/stats", "Read upload stats"),
        _api_action("/api/gallery/upload", "Upload media to Gallery", method="POST", writes=True, requires_approval=True),
        _api_action("/api/upload", "Upload chat attachment", method="POST", writes=True, requires_approval=True),
        _api_action("/api/upload/{file_id}/vision", "Refresh uploaded image vision cache", method="GET", writes=True, requires_approval=True),
        _api_action("/api/gallery/style-transfer", "Run AI media style transfer", method="POST", writes=True, requires_approval=True),
        _api_action("/api/gallery/ai-upscale", "Run AI media upscale", method="POST", writes=True, requires_approval=True),
        _api_action("/api/gallery/{image_id}/replace", "Replace gallery media file", method="POST", writes=True, requires_approval=True),
        _api_action("/api/gallery/{image_id}", "Delete gallery media file", method="DELETE", writes=True, deletes=True, requires_approval=True),
        _api_action("/api/gallery/download-zip", "Export selected gallery media", method="POST", writes=True, requires_approval=True),
    ]
    return {
        "mode": "read-only-gallery-media-plan",
        "generated_at": _utc_now(),
        "owner": owner,
        "summary": {
            "state": "error" if any(row.get("state") == "error" for row in alert_rows) else ("warn" if alert_rows else "ok"),
            "media_file_count": total_files,
            "media_size_bytes": total_size,
            "gallery_enabled": gallery_enabled,
            "image_generation_enabled": image_generation_enabled,
            "vision_enabled": vision_enabled,
            "offline": offline_state,
            "gallery_alert_count": len(alert_rows),
            "critical_gallery_alert_count": len([row for row in alert_rows if row.get("state") == "error"]),
            "entry_route_count": len(entry_rows),
            "entry_route_ready_count": len([row for row in entry_rows if row.get("state") == "ok"]),
            "handoff_count": len(handoff_rows),
            "handoff_ready_count": len([row for row in handoff_rows if row.get("state") == "ok"]),
            "uploads_files": False,
            "generates_images": False,
            "edits_media": False,
            "deletes_media": False,
            "exports_media": False,
            "uses_network": False,
        },
        "media_rows": media_rows,
        "alert_rows": alert_rows,
        "entry_rows": entry_rows,
        "handoff_rows": handoff_rows,
        "api_actions": api_actions,
        "approval": {
            "required": False,
            "gate": "Gallery readiness only",
            "policy": (
                "This endpoint only inspects local media paths, upload/image settings, and API gates. "
                "It does not upload files, generate images, edit media, replace images, delete media, "
                "export media, refresh vision captions, call model endpoints, or use network access."
            ),
        },
        "paths": {
            "generated_images": str(data_path / "generated_images"),
            "gallery": str(data_path / "gallery"),
            "gallery_uploads": str(data_path / "gallery_uploads"),
            "uploads": str(upload_path),
            "vision_cache": str(upload_path / ".vision"),
            "settings": "data/settings.json",
            "features": "data/features.json",
        },
    }
