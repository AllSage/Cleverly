"""Backup routes - export/import user data."""

import base64
import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.settings import load_features, load_settings, save_features, save_settings

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


def _derive_backup_key(password: str, salt: bytes, iterations: int = BACKUP_KDF_ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def setup_backup_routes(memory_manager, preset_manager, skills_manager) -> APIRouter:
    router = APIRouter(tags=["backup"])

    def _build_export_payload(user: str | None) -> dict:
        from routes.prefs_routes import _load_for_user

        return {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "exported_by": user,
            "memories": memory_manager.load(owner=user),
            "presets": preset_manager.get_all(),
            "skills": skills_manager.load(owner=user),
            "settings": load_settings(),
            "features": load_features(),
            "preferences": _load_for_user(user),
        }

    def _import_payload(body: dict, user: str | None) -> list[str]:
        imported = []

        if "memories" in body and isinstance(body["memories"], list):
            existing = memory_manager.load_all()
            existing_texts = {e.get("text", "").strip().lower() for e in existing}
            added = 0
            for mem in body["memories"]:
                if not isinstance(mem, dict) or not mem.get("text"):
                    continue
                if mem["text"].strip().lower() in existing_texts:
                    continue
                if user and not mem.get("owner"):
                    mem["owner"] = user
                existing.append(mem)
                existing_texts.add(mem["text"].strip().lower())
                added += 1
            memory_manager.save(existing)
            imported.append(f"{added} memories")

        if "skills" in body and isinstance(body["skills"], list):
            existing = skills_manager.load_all()
            existing_ids = {s.get("id") for s in existing}
            existing_titles = {s.get("title", "").strip().lower() for s in existing}
            added = 0
            for skill in body["skills"]:
                if not isinstance(skill, dict) or not skill.get("title"):
                    continue
                if skill.get("id") in existing_ids:
                    continue
                if skill["title"].strip().lower() in existing_titles:
                    continue
                if user and not skill.get("owner"):
                    skill["owner"] = user
                existing.append(skill)
                existing_ids.add(skill.get("id"))
                existing_titles.add(skill["title"].strip().lower())
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
            current.update(body["settings"])
            save_settings(current)
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
        payload = _build_export_payload(user)
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
        imported = _import_payload(payload, user)
        if not imported:
            return {"ok": False, "message": "No recognized data found in the encrypted backup"}
        return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}

    return router
