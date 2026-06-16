import json
import importlib.util
import time
from pathlib import Path


def _load_auth_module():
    path = Path(__file__).resolve().parents[1] / "core" / "auth.py"
    spec = importlib.util.spec_from_file_location("core_auth_manager_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auth = _load_auth_module()


def _fast_passwords(monkeypatch):
    monkeypatch.setattr(auth, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(auth, "_verify_password", lambda password, hashed: hashed == f"hash:{password}")


def test_auth_manager_helpers_and_defensive_edges(monkeypatch, tmp_path):
    real_hash = auth._hash_password("pw")
    assert auth._verify_password("pw", real_hash) is True
    assert auth._verify_password("bad", real_hash) is False

    _fast_passwords(monkeypatch)
    auth_path = tmp_path / "auth.json"
    auth_path.write_text("{", encoding="utf-8")
    (tmp_path / "sessions.json").write_text("{", encoding="utf-8")
    broken = auth.AuthManager(str(auth_path))
    assert broken.users == {}
    assert broken._sessions == {}

    manager_path = tmp_path / "manager.json"
    manager = auth.AuthManager(str(manager_path))
    assert manager.setup("admin", "pw") is True
    assert manager.create_user("bob", "pw") is True
    assert manager.create_user("carol", "pw") is True
    assert manager.delete_user("bob", "bob") is False
    assert manager.delete_user("bob", "carol") is False
    assert manager.set_privileges("missing", {"can_use_bash": True}) is False
    assert manager.verify_password("missing", "pw") is False
    assert manager.validate_token(None) is False
    assert manager.totp_confirm_enable("bob", "123456") is False

    monkeypatch.setattr(auth, "_atomic_write_json", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    manager._save_sessions()


def test_auth_manager_account_tokens_privileges_and_status(monkeypatch, tmp_path):
    _fast_passwords(monkeypatch)
    auth_path = tmp_path / "auth.json"
    manager = auth.AuthManager(str(auth_path))

    assert manager.is_configured is False
    assert manager.signup_enabled is False
    manager.signup_enabled = True
    assert manager.signup_enabled is True

    assert manager.setup("Admin", "pw") is True
    assert manager.setup("Second", "pw") is False
    assert manager.is_admin("admin") is True
    assert manager.create_user("admin", "pw") is False
    assert manager.set_privileges("admin", {"can_use_bash": True}) is False

    assert manager.create_user("Bob", "pw2") is True
    assert manager.verify_password("bob", "bad") is False
    assert manager.verify_password("bob", "pw2") is True
    assert manager.change_password("missing", "pw", "new") is False
    assert manager.change_password("bob", "bad", "new") is False
    assert manager.change_password("bob", "pw2", "new") is True
    assert manager.verify_password("bob", "new") is True

    assert manager.set_privileges("bob", {"can_use_bash": True, "unknown": True}) is True
    privileges = manager.get_privileges("bob")
    assert privileges["can_use_bash"] is True
    assert "unknown" not in privileges
    listed = {item["username"]: item for item in manager.list_users()}
    assert listed["admin"]["is_admin"] is True
    assert listed["bob"]["privileges"]["can_use_bash"] is True

    assert manager.create_session("bob", "bad") is None
    bob_token = manager.create_session("bob", "new")
    assert bob_token
    assert manager.validate_token(bob_token) is True
    assert manager.get_username_for_token(bob_token) == "bob"
    assert manager.status(bob_token)["authenticated"] is True
    assert manager.status(None) == {
        "configured": True,
        "authenticated": False,
        "username": None,
        "is_admin": False,
    }

    assert manager.delete_user("missing", "admin") is False
    assert manager.delete_user("admin", "admin") is False
    assert manager.delete_user("bob", "bob") is False
    assert manager.delete_user("bob", "admin") is True
    assert manager.validate_token(bob_token) is False

    assert manager.create_user("alice", "pw") is True
    assert manager.create_user("carol", "pw") is True
    alice_token = manager.create_session("alice", "pw")
    assert manager.rename_user("", "renamed", "admin") is False
    assert manager.rename_user("missing", "renamed", "admin") is False
    assert manager.rename_user("alice", "carol", "admin") is False
    assert manager.rename_user("alice", "renamed", "carol") is False
    assert manager.rename_user("alice", "renamed", "admin") is True
    assert manager.get_username_for_token(alice_token) == "renamed"

    manager.revoke_token(alice_token)
    assert manager.get_username_for_token(alice_token) is None


def test_auth_manager_session_expiry_orphans_and_load_migrations(monkeypatch, tmp_path):
    _fast_passwords(monkeypatch)
    auth_path = tmp_path / "auth.json"
    sessions_path = tmp_path / "sessions.json"
    auth_path.write_text(
        json.dumps({
            "users": {
                "MixedCase": {
                    "password_hash": "hash:pw",
                    "role": "admin",
                    "created": 1,
                }
            }
        }),
        encoding="utf-8",
    )
    sessions_path.write_text(
        json.dumps({
            "valid": {"username": "mixedcase", "expiry": time.time() + 60},
            "expired": {"username": "mixedcase", "expiry": time.time() - 60},
        }),
        encoding="utf-8",
    )

    manager = auth.AuthManager(str(auth_path))
    assert "MixedCase" not in manager.users
    assert manager.is_admin("mixedcase") is True
    assert set(manager._sessions) == {"valid"}

    manager._sessions["old"] = {"username": "mixedcase", "expiry": time.time() - 1}
    assert manager.validate_token("old") is False
    manager._sessions["old2"] = {"username": "mixedcase", "expiry": time.time() - 1}
    assert manager.get_username_for_token("old2") is None
    manager._sessions["ghost"] = {"username": "ghost", "expiry": time.time() + 60}
    assert manager.validate_token("ghost") is False
    manager._sessions["ghost2"] = {"username": "ghost", "expiry": time.time() + 60}
    assert manager.get_username_for_token("ghost2") is None

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps({"username": "Root", "password_hash": "hash:pw"}),
        encoding="utf-8",
    )
    legacy = auth.AuthManager(str(legacy_path))
    assert legacy.is_admin("Root") is True


def test_auth_manager_totp_lifecycle(monkeypatch, tmp_path):
    _fast_passwords(monkeypatch)
    auth_path = tmp_path / "auth.json"
    manager = auth.AuthManager(str(auth_path))
    assert manager.create_user("alice", "pw") is True

    class FakeTotp:
        def __init__(self, secret):
            self.secret = secret

        def provisioning_uri(self, name, issuer_name):
            return f"otpauth://totp/{issuer_name}:{name}?secret={self.secret}"

        def verify(self, code, valid_window=0):
            return code == "123456" and valid_window == 1

    monkeypatch.setattr(auth.pyotp, "random_base32", lambda: "SECRET")
    monkeypatch.setattr(auth.pyotp, "TOTP", FakeTotp)
    backup_codes = iter([f"backup{i}" for i in range(8)])
    monkeypatch.setattr(auth.secrets, "token_hex", lambda n: next(backup_codes))

    assert manager.totp_enabled("missing") is False
    assert manager.totp_generate_secret("missing") is None
    secret = manager.totp_generate_secret("alice")
    assert secret == "SECRET"
    assert "Cleverly:alice" in manager.totp_get_provisioning_uri("alice", secret)
    assert manager.totp_confirm_enable("alice", "000000") is False
    assert manager.totp_confirm_enable("alice", "123456") is True
    assert manager.totp_enabled("alice") is True

    first_backup = manager.users["alice"]["totp_backup_codes"][0]
    assert manager.totp_verify("alice", first_backup) is True
    assert first_backup not in manager.users["alice"]["totp_backup_codes"]
    assert manager.totp_verify("alice", "123456") is True
    assert manager.totp_verify("alice", "bad") is False

    assert manager.totp_disable("alice", "bad") is False
    assert manager.totp_disable("alice", "pw") is True
    assert manager.totp_enabled("alice") is False
    assert manager.totp_verify("alice", "anything") is True

    manager.users["alice"]["totp_enabled"] = True
    manager.users["alice"].pop("totp_secret", None)
    assert manager.totp_verify("alice", "anything") is True
