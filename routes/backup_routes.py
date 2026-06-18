"""Backup routes - export/import user data."""

import base64
import json
import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.settings import load_features, load_settings, save_features, save_settings
from src.settings_scrub import is_secret_key, scrub_settings

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

BACKUP_KDF_ITERATIONS = 390_000


class EncryptedBackupExportRequest(BaseModel):
    password: str = Field(min_length=8, max_length=4096)


class EncryptedBackupImportRequest(BaseModel):
    password: str = Field(min_length=8, max_length=4096)
    backup: dict
    dry_run: bool = False


def _derive_backup_key(password: str, salt: bytes, iterations: int = BACKUP_KDF_ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _summarize_backup_payload(payload: dict) -> dict:
    def count_value(value) -> int:
        if isinstance(value, (list, tuple, set)):
            return len(value)
        if isinstance(value, dict):
            return len(value)
        return 1 if value else 0

    recognized = {}
    for key in ("memories", "presets", "skills", "settings", "features", "preferences"):
        if key in payload:
            recognized[key] = count_value(payload.get(key))
    return {
        "version": payload.get("version"),
        "exported_at": payload.get("exported_at", ""),
        "exported_by": payload.get("exported_by", ""),
        "recognized": recognized,
        "recognized_sections": sorted(recognized.keys()),
    }


def _strip_blank_secret_values(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if is_secret_key(str(key)) and isinstance(item, str) and item == "":
                continue
            cleaned[key] = _strip_blank_secret_values(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_blank_secret_values(item) for item in value]
    return value


def _merge_import_settings(current: dict, incoming: dict) -> dict:
    def merge(existing, value):
        value = _strip_blank_secret_values(value)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged = dict(existing)
            for key, item in value.items():
                merged[key] = merge(merged.get(key), item)
            return merged
        if isinstance(existing, list) and isinstance(value, list):
            merged = list(existing)
            for index, item in enumerate(value):
                if index < len(merged):
                    merged[index] = merge(merged[index], item)
                else:
                    merged.append(item)
            return merged
        return value

    return merge(current or {}, incoming or {})


def setup_backup_routes(memory_manager, preset_manager, skills_manager) -> APIRouter:
    router = APIRouter(tags=["backup"])

    def _build_export_payload(user: str | None, *, include_secrets: bool = False) -> dict:
        from routes.prefs_routes import _load_for_user
        settings = load_settings()

        return {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "exported_by": user,
            "memories": memory_manager.load(owner=user),
            "presets": preset_manager.get_all(),
            "skills": skills_manager.load(owner=user),
            "settings": settings if include_secrets else scrub_settings(settings),
            "features": load_features(),
            "preferences": _load_for_user(user),
        }

    def _import_payload(body: dict, user: str | None) -> list[str]:
        imported = []

        if "memories" in body and isinstance(body["memories"], list):
            existing = memory_manager.load_all()
            existing_ids = {e.get("id") for e in existing if e.get("id")}
            if user:
                existing_texts = {
                    e.get("text", "").strip().lower()
                    for e in existing
                    if e.get("owner") == user
                }
            else:
                existing_texts = {e.get("text", "").strip().lower() for e in existing}
            added = 0
            for mem in body["memories"]:
                if not isinstance(mem, dict) or not mem.get("text"):
                    continue
                mem_new = dict(mem)
                if user:
                    mem_new["owner"] = user
                if mem_new.get("id") in existing_ids:
                    mem_new["id"] = str(uuid.uuid4())
                text_key = mem_new["text"].strip().lower()
                if text_key in existing_texts:
                    continue
                existing.append(mem_new)
                if mem_new.get("id"):
                    existing_ids.add(mem_new.get("id"))
                existing_texts.add(text_key)
                added += 1
            memory_manager.save(existing)
            imported.append(f"{added} memories")

        if "skills" in body and isinstance(body["skills"], list):
            existing = skills_manager.load_all()
            existing_ids_all = {s.get("id") for s in existing if s.get("id")}
            if user:
                existing_for_user = [s for s in existing if s.get("owner") == user]
            else:
                existing_for_user = existing
            existing_ids = {s.get("id") for s in existing_for_user}
            existing_titles = {s.get("title", "").strip().lower() for s in existing_for_user}
            added = 0
            for skill in body["skills"]:
                if not isinstance(skill, dict) or not skill.get("title"):
                    continue
                skill_new = dict(skill)
                if user:
                    skill_new["owner"] = user
                if skill_new.get("id") in existing_ids_all:
                    skill_new["id"] = str(uuid.uuid4())
                if skill_new.get("id") in existing_ids:
                    continue
                title_key = skill_new["title"].strip().lower()
                if title_key in existing_titles:
                    continue
                existing.append(skill_new)
                if skill_new.get("id"):
                    existing_ids.add(skill_new.get("id"))
                    existing_ids_all.add(skill_new.get("id"))
                existing_titles.add(title_key)
                added += 1
            skills_manager.save(existing)
            imported.append(f"{added} skills")

        if "presets" in body and isinstance(body["presets"], dict):
            current = preset_manager.get_all()
            for key, value in body["presets"].items():
                if isinstance(value, dict):
                    current[key] = value
                elif isinstance(value, list):
                    current[key] = value
            preset_manager.save(current)
            imported.append("presets")

        if "settings" in body and isinstance(body["settings"], dict):
            current = load_settings()
            save_settings(_merge_import_settings(current, body["settings"]))
            imported.append("settings")

        if "features" in body and isinstance(body["features"], dict):
            current = load_features()
            current.update(body["features"])
            save_features(current)
            imported.append("features")

        if "preferences" in body and isinstance(body["preferences"], dict):
            from routes.prefs_routes import _load_for_user, _save_for_user

            current = _load_for_user(user)
            current.update(body["preferences"])
            _save_for_user(user, current)
            imported.append("preferences")

        return imported

    @router.get("/api/export")
    async def export_data(request: Request):
        """Export all user data as a downloadable JSON file."""
        require_admin(request)
        user = get_current_user(request)
        export_data = _build_export_payload(user)

        filename = f"cleverly_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=json.dumps(export_data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @router.post("/api/import")
    async def import_data(request: Request):
        """Import user data from a previously exported JSON file."""
        require_admin(request)
        user = get_current_user(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object")

        imported = _import_payload(body, user)
        if not imported:
            return {"ok": False, "message": "No recognized data found in the file"}
        return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}

    @router.post("/api/backup/encrypted/export")
    async def export_encrypted_data(body: EncryptedBackupExportRequest, request: Request):
        """Export user data as a password-encrypted backup bundle."""
        require_admin(request)
        user = get_current_user(request)
        salt = os.urandom(16)
        key = _derive_backup_key(body.password, salt)
        payload = _build_export_payload(user, include_secrets=True)
        token = Fernet(key).encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        encrypted = {
            "format": "cleverly.encrypted-backup.v1",
            "encrypted": True,
            "cipher": "fernet",
            "kdf": "pbkdf2-sha256",
            "iterations": BACKUP_KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "token": token.decode("ascii"),
            "exported_at": datetime.now().isoformat(),
        }
        filename = f"cleverly_encrypted_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=json.dumps(encrypted, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @router.post("/api/backup/encrypted/import")
    async def import_encrypted_data(body: EncryptedBackupImportRequest, request: Request):
        """Decrypt and import a password-encrypted Cleverly backup."""
        require_admin(request)
        user = get_current_user(request)
        backup = body.backup
        if backup.get("format") != "cleverly.encrypted-backup.v1":
            raise HTTPException(400, "Unsupported encrypted backup format")
        try:
            iterations = int(backup.get("iterations") or BACKUP_KDF_ITERATIONS)
            salt = base64.b64decode(str(backup.get("salt") or ""), validate=True)
            token = str(backup.get("token") or "").encode("ascii")
            key = _derive_backup_key(body.password, salt, iterations)
            decrypted = Fernet(key).decrypt(token)
            payload = json.loads(decrypted.decode("utf-8"))
        except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
            raise HTTPException(400, "Invalid password or encrypted backup")
        if not isinstance(payload, dict):
            raise HTTPException(400, "Encrypted backup did not contain a JSON object")
        if body.dry_run:
            summary = _summarize_backup_payload(payload)
            if not summary["recognized"]:
                return {"ok": False, "dry_run": True, "message": "No recognized data found in the encrypted backup", "summary": summary}
            return {
                "ok": True,
                "dry_run": True,
                "message": "Restore drill passed. The encrypted backup decrypted and recognized data sections.",
                "summary": summary,
            }
        imported = _import_payload(payload, user)
        if not imported:
            return {"ok": False, "message": "No recognized data found in the encrypted backup"}
        return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}

    return router
