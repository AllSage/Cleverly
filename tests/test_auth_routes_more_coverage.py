import asyncio
import sys
import types
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from routes import auth_routes


def _endpoint(router, path, method="GET"):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not registered")


class RequestLike:
    def __init__(self, *, token=None, host="127.0.0.1", body=None, headers=None):
        self.cookies = {}
        if token:
            self.cookies[auth_routes.SESSION_COOKIE] = token
        self.client = SimpleNamespace(host=host)
        self._body = body or {}
        self.headers = headers or {}

    async def json(self):
        return dict(self._body)


class ResponseLike:
    def __init__(self):
        self.cookies = {}
        self.deleted = []

    def set_cookie(self, **kwargs):
        self.cookies[kwargs["key"]] = kwargs

    def delete_cookie(self, key, **kwargs):
        self.deleted.append((key, kwargs))


class FakeAuth:
    def __init__(self):
        self.is_configured = False
        self.signup_enabled = False
        self.users = {}
        self.tokens = {}
        self.revoked = []
        self.fail_setup = False
        self.fail_session = False
        self.fail_privileges = False

    def setup(self, username, password):
        if self.fail_setup:
            return False
        return self.create_user(username, password, True)

    def create_user(self, username, password, is_admin=False):
        username = username.strip().lower()
        if username in self.users:
            return False
        self.users[username] = {
            "password": password,
            "is_admin": is_admin,
            "privileges": {"can_use_bash": bool(is_admin)},
            "totp_enabled": False,
        }
        self.is_configured = True
        return True

    def verify_password(self, username, password):
        user = self.users.get(username.strip().lower())
        return bool(user and user["password"] == password)

    def create_session(self, username, password):
        if self.fail_session or not self.verify_password(username, password):
            return None
        token = f"tok-{username}"
        self.tokens[token] = username
        return token

    def get_username_for_token(self, token):
        return self.tokens.get(token)

    def revoke_token(self, token):
        self.revoked.append(token)
        self.tokens.pop(token, None)

    def status(self, token):
        username = self.get_username_for_token(token)
        return {
            "configured": self.is_configured,
            "authenticated": bool(username),
            "username": username,
            "is_admin": self.is_admin(username) if username else False,
        }

    def get_privileges(self, username):
        if self.fail_privileges:
            raise RuntimeError("privilege store down")
        return dict(self.users.get(username, {}).get("privileges", {}))

    def is_admin(self, username):
        return bool(self.users.get(username, {}).get("is_admin"))

    def list_users(self):
        return [{"username": name, "is_admin": data["is_admin"]} for name, data in sorted(self.users.items())]

    def set_privileges(self, username, body):
        if username not in self.users or self.users[username].get("is_admin"):
            return False
        self.users[username]["privileges"].update(body)
        return True

    def change_password(self, username, current, new):
        if not self.verify_password(username, current):
            return False
        self.users[username]["password"] = new
        return True

    def delete_user(self, username, requesting_user):
        username = username.strip().lower()
        if username not in self.users or username == requesting_user:
            return False
        del self.users[username]
        return True

    def rename_user(self, old, new, requesting_user):
        if old not in self.users or new in self.users:
            return False
        self.users[new] = self.users.pop(old)
        for token, username in list(self.tokens.items()):
            if username == old:
                self.tokens[token] = new
        return True

    def totp_enabled(self, username):
        return bool(self.users.get(username, {}).get("totp_enabled"))

    def totp_generate_secret(self, username):
        if username not in self.users:
            return None
        self.users[username]["pending_secret"] = "SECRET"
        return "SECRET"

    def totp_get_provisioning_uri(self, username, secret):
        return f"otpauth://totp/Cleverly:{username}?secret={secret}"

    def totp_confirm_enable(self, username, code):
        if code != "123456":
            return False
        self.users[username]["totp_enabled"] = True
        self.users[username]["totp_backup_codes"] = ["backup"]
        return True

    def totp_verify(self, username, code):
        return code == "123456" or code in self.users.get(username, {}).get("totp_backup_codes", [])

    def totp_disable(self, username, password):
        if not self.verify_password(username, password):
            return False
        self.users[username]["totp_enabled"] = False
        return True


def _auth_with_users():
    auth = FakeAuth()
    auth.create_user("admin", "password123", True)
    auth.create_user("alice", "password123", False)
    auth.tokens["tok-admin"] = "admin"
    auth.tokens["tok-alice"] = "alice"
    return auth


def test_setup_signup_login_logout_status_password_and_2fa(monkeypatch):
    auth = FakeAuth()
    router = auth_routes.setup_auth_routes(auth)

    setup = _endpoint(router, "/api/auth/setup", "POST")
    with pytest.raises(HTTPException) as short_setup:
        asyncio.run(setup(auth_routes.SetupRequest(username="admin", password="short"), RequestLike()))
    assert short_setup.value.status_code == 400
    auth.fail_setup = True
    with pytest.raises(HTTPException) as failed_setup:
        asyncio.run(setup(auth_routes.SetupRequest(username="admin", password="password123"), RequestLike(host="127.0.0.2")))
    assert failed_setup.value.status_code == 500
    auth.fail_setup = False
    assert asyncio.run(setup(auth_routes.SetupRequest(username="admin", password="password123"), RequestLike(host="127.0.0.3"))) == {
        "ok": True,
        "message": "Admin account created",
    }
    with pytest.raises(HTTPException) as configured:
        asyncio.run(setup(auth_routes.SetupRequest(username="root", password="password123"), RequestLike(host="127.0.0.4")))
    assert configured.value.status_code == 400

    signup = _endpoint(router, "/api/auth/signup", "POST")
    with pytest.raises(HTTPException) as disabled:
        asyncio.run(signup(auth_routes.SignupRequest(username="alice", password="password123"), RequestLike()))
    assert disabled.value.status_code == 403
    auth.signup_enabled = True
    with pytest.raises(HTTPException) as short_signup:
        asyncio.run(signup(auth_routes.SignupRequest(username="alice", password="short"), RequestLike(host="127.0.0.5")))
    assert short_signup.value.status_code == 400
    with pytest.raises(HTTPException) as empty_user:
        asyncio.run(signup(auth_routes.SignupRequest(username=" ", password="password123"), RequestLike(host="127.0.0.6")))
    assert empty_user.value.status_code == 400
    assert asyncio.run(signup(auth_routes.SignupRequest(username="Alice", password="password123"), RequestLike(host="127.0.0.7")))["ok"] is True
    with pytest.raises(HTTPException) as duplicate:
        asyncio.run(signup(auth_routes.SignupRequest(username="alice", password="password123"), RequestLike(host="127.0.0.8")))
    assert duplicate.value.status_code == 409

    login = _endpoint(router, "/api/auth/login", "POST")
    response = ResponseLike()
    with pytest.raises(HTTPException) as invalid_login:
        asyncio.run(login(auth_routes.LoginRequest(username="alice", password="bad"), RequestLike(), response))
    assert invalid_login.value.status_code == 401
    auth.users["alice"]["totp_enabled"] = True
    assert asyncio.run(login(auth_routes.LoginRequest(username="alice", password="password123"), RequestLike(host="127.0.0.9"), response)) == {
        "ok": False,
        "requires_totp": True,
        "username": "alice",
    }
    with pytest.raises(HTTPException) as bad_totp:
        asyncio.run(login(auth_routes.LoginRequest(username="alice", password="password123", totp_code="000000"), RequestLike(host="127.0.0.10"), response))
    assert bad_totp.value.status_code == 401
    auth.users["alice"]["totp_enabled"] = False
    auth.fail_session = True
    with pytest.raises(HTTPException) as no_token:
        asyncio.run(login(auth_routes.LoginRequest(username="alice", password="password123"), RequestLike(host="127.0.0.11"), response))
    assert no_token.value.status_code == 401
    auth.fail_session = False
    response = ResponseLike()
    assert asyncio.run(login(auth_routes.LoginRequest(username="alice", password="password123", remember=False), RequestLike(host="127.0.0.12"), response)) == {
        "ok": True,
        "username": "alice",
    }
    assert "max_age" not in response.cookies[auth_routes.SESSION_COOKIE]
    response = ResponseLike()
    assert asyncio.run(login(auth_routes.LoginRequest(username="alice", password="password123"), RequestLike(host="127.0.0.13"), response))["ok"] is True
    assert response.cookies[auth_routes.SESSION_COOKIE]["max_age"] == 60 * 60 * 24 * 7

    status = _endpoint(router, "/api/auth/status")
    assert asyncio.run(status(RequestLike(token="tok-alice")))["privileges"] == {"can_use_bash": False}
    auth.fail_privileges = True
    assert asyncio.run(status(RequestLike(token="tok-alice")))["authenticated"] is True
    auth.fail_privileges = False
    monkeypatch.setenv("AUTH_ENABLED", "false")
    local_status = asyncio.run(status(RequestLike()))
    assert local_status["authenticated"] is True
    assert local_status["username"] == "local"
    assert local_status["is_admin"] is True
    assert local_status["auth_disabled"] is True
    assert local_status["privileges"]["can_use_agent"] is True
    proxied_status = asyncio.run(status(RequestLike(headers={"x-forwarded-for": "198.51.100.20"})))
    assert proxied_status["authenticated"] is False
    assert "auth_disabled" not in proxied_status
    monkeypatch.delenv("AUTH_ENABLED", raising=False)

    change = _endpoint(router, "/api/auth/change-password", "POST")
    with pytest.raises(HTTPException) as unauth_change:
        asyncio.run(change(auth_routes.ChangePasswordRequest(current_password="password123", new_password="newpassword"), RequestLike()))
    assert unauth_change.value.status_code == 401
    with pytest.raises(HTTPException) as short_new:
        asyncio.run(change(auth_routes.ChangePasswordRequest(current_password="password123", new_password="short"), RequestLike(token="tok-alice")))
    assert short_new.value.status_code == 400
    with pytest.raises(HTTPException) as wrong_current:
        asyncio.run(change(auth_routes.ChangePasswordRequest(current_password="wrong", new_password="newpassword"), RequestLike(token="tok-alice")))
    assert wrong_current.value.status_code == 400
    assert asyncio.run(change(auth_routes.ChangePasswordRequest(current_password="password123", new_password="newpassword"), RequestLike(token="tok-alice"))) == {"ok": True}

    qrcode = types.ModuleType("qrcode")

    class QR:
        def save(self, buf, format):
            buf.write(b"png")

    qrcode.make = lambda *args, **kwargs: QR()
    monkeypatch.setitem(sys.modules, "qrcode", qrcode)

    setup_2fa = _endpoint(router, "/api/auth/2fa/setup", "POST")
    confirm_2fa = _endpoint(router, "/api/auth/2fa/confirm", "POST")
    disable_2fa = _endpoint(router, "/api/auth/2fa/disable", "POST")
    status_2fa = _endpoint(router, "/api/auth/2fa/status")
    with pytest.raises(HTTPException) as unauth_2fa:
        asyncio.run(setup_2fa(RequestLike()))
    assert unauth_2fa.value.status_code == 401
    result = asyncio.run(setup_2fa(RequestLike(token="tok-alice")))
    assert result["secret"] == "SECRET"
    assert result["qr_code"].startswith("data:image/png;base64,")
    auth.users["alice"]["totp_enabled"] = True
    with pytest.raises(HTTPException) as already_2fa:
        asyncio.run(setup_2fa(RequestLike(token="tok-alice")))
    assert already_2fa.value.status_code == 400
    auth.users["alice"]["totp_enabled"] = False
    monkeypatch.setattr(auth, "totp_generate_secret", lambda _user: None)
    with pytest.raises(HTTPException) as secret_failed:
        asyncio.run(setup_2fa(RequestLike(token="tok-alice")))
    assert secret_failed.value.status_code == 500
    monkeypatch.setattr(auth, "totp_generate_secret", lambda user: FakeAuth.totp_generate_secret(auth, user))
    with pytest.raises(HTTPException) as bad_confirm:
        asyncio.run(confirm_2fa(auth_routes.setup_auth_routes(auth).routes[0].endpoint if False else SimpleNamespace(code="bad"), RequestLike(token="tok-alice")))
    assert bad_confirm.value.status_code == 400
    with pytest.raises(HTTPException) as unauth_confirm:
        asyncio.run(confirm_2fa(SimpleNamespace(code="123456"), RequestLike()))
    assert unauth_confirm.value.status_code == 401
    assert asyncio.run(confirm_2fa(SimpleNamespace(code="123456"), RequestLike(token="tok-alice"))) == {
        "ok": True,
        "backup_codes": ["backup"],
    }
    assert asyncio.run(status_2fa(RequestLike(token="tok-alice"))) == {"enabled": True}
    with pytest.raises(HTTPException) as unauth_status:
        asyncio.run(status_2fa(RequestLike()))
    assert unauth_status.value.status_code == 401
    with pytest.raises(HTTPException) as unauth_disable:
        asyncio.run(disable_2fa(SimpleNamespace(password="bad"), RequestLike()))
    assert unauth_disable.value.status_code == 401
    with pytest.raises(HTTPException) as bad_disable:
        asyncio.run(disable_2fa(SimpleNamespace(password="bad"), RequestLike(token="tok-alice")))
    assert bad_disable.value.status_code == 400
    auth.users["alice"]["password"] = "newpassword"
    assert asyncio.run(disable_2fa(SimpleNamespace(password="newpassword"), RequestLike(token="tok-alice"))) == {"ok": True}

    logout = _endpoint(router, "/api/auth/logout", "POST")
    logout_response = ResponseLike()
    assert asyncio.run(logout(RequestLike(token="tok-alice"), logout_response)) == {"ok": True}
    assert "tok-alice" in auth.revoked
    assert logout_response.deleted[0][0] == auth_routes.SESSION_COOKIE


def test_auth_rate_limits_and_signup_before_setup():
    auth = FakeAuth()
    router = auth_routes.setup_auth_routes(auth)
    signup = _endpoint(router, "/api/auth/signup", "POST")
    auth.signup_enabled = True
    with pytest.raises(HTTPException) as before_setup:
        asyncio.run(signup(auth_routes.SignupRequest(username="new", password="password123"), RequestLike(host="10.0.0.1")))
    assert before_setup.value.status_code == 400

    setup = _endpoint(router, "/api/auth/setup", "POST")
    for _ in range(3):
        with pytest.raises(HTTPException):
            asyncio.run(setup(auth_routes.SetupRequest(username="admin", password="short"), RequestLike(host="10.0.0.2")))
    with pytest.raises(HTTPException) as setup_limited:
        asyncio.run(setup(auth_routes.SetupRequest(username="admin", password="short"), RequestLike(host="10.0.0.2")))
    assert setup_limited.value.status_code == 429

    configured = _auth_with_users()
    configured.signup_enabled = True
    router = auth_routes.setup_auth_routes(configured)
    signup = _endpoint(router, "/api/auth/signup", "POST")
    for _ in range(3):
        with pytest.raises(HTTPException):
            asyncio.run(signup(auth_routes.SignupRequest(username="new", password="short"), RequestLike(host="10.0.0.3")))
    with pytest.raises(HTTPException) as signup_limited:
        asyncio.run(signup(auth_routes.SignupRequest(username="new", password="short"), RequestLike(host="10.0.0.3")))
    assert signup_limited.value.status_code == 429

    login = _endpoint(router, "/api/auth/login", "POST")
    for _ in range(15):
        with pytest.raises(HTTPException):
            asyncio.run(login(auth_routes.LoginRequest(username="alice", password="bad"), RequestLike(host="10.0.0.4"), ResponseLike()))
    with pytest.raises(HTTPException) as login_limited:
        asyncio.run(login(auth_routes.LoginRequest(username="alice", password="bad"), RequestLike(host="10.0.0.4"), ResponseLike()))
    assert login_limited.value.status_code == 429


def test_admin_features_settings_integrations_and_rename(monkeypatch):
    auth = _auth_with_users()
    router = auth_routes.setup_auth_routes(auth)

    users = _endpoint(router, "/api/auth/users")
    with pytest.raises(HTTPException) as denied_users:
        asyncio.run(users(RequestLike(token="tok-alice")))
    assert denied_users.value.status_code == 403
    assert asyncio.run(users(RequestLike(token="tok-admin")))["users"][0]["username"] == "admin"

    create = _endpoint(router, "/api/auth/users", "POST")
    with pytest.raises(HTTPException) as denied_create:
        asyncio.run(create(auth_routes.CreateUserRequest(username="bob", password="password123"), RequestLike(token="tok-alice")))
    assert denied_create.value.status_code == 403
    with pytest.raises(HTTPException) as short_create:
        asyncio.run(create(auth_routes.CreateUserRequest(username="bob", password="short"), RequestLike(token="tok-admin")))
    assert short_create.value.status_code == 400
    assert asyncio.run(create(auth_routes.CreateUserRequest(username="bob", password="password123"), RequestLike(token="tok-admin"))) == {"ok": True}
    with pytest.raises(HTTPException) as dup_create:
        asyncio.run(create(auth_routes.CreateUserRequest(username="bob", password="password123"), RequestLike(token="tok-admin")))
    assert dup_create.value.status_code == 409

    privileges = _endpoint(router, "/api/auth/users/{username}/privileges", "PUT")
    with pytest.raises(HTTPException) as denied_privs:
        asyncio.run(privileges("bob", RequestLike(token="tok-alice", body={"can_use_bash": True})))
    assert denied_privs.value.status_code == 403
    with pytest.raises(HTTPException) as admin_privs:
        asyncio.run(privileges("admin", RequestLike(token="tok-admin", body={"can_use_bash": False})))
    assert admin_privs.value.status_code == 404
    assert asyncio.run(privileges("bob", RequestLike(token="tok-admin", body={"can_use_bash": True})))["privileges"]["can_use_bash"] is True

    rename = _endpoint(router, "/api/auth/users/{username}/rename", "PUT")
    with pytest.raises(HTTPException) as denied_rename:
        asyncio.run(rename("bob", auth_routes.RenameUserRequest(username="robert"), RequestLike(token="tok-alice")))
    assert denied_rename.value.status_code == 403
    with pytest.raises(HTTPException) as empty_rename:
        asyncio.run(rename("bob", auth_routes.RenameUserRequest(username=" "), RequestLike(token="tok-admin")))
    assert empty_rename.value.status_code == 400
    assert asyncio.run(rename("bob", auth_routes.RenameUserRequest(username="bob"), RequestLike(token="tok-admin"))) == {
        "ok": True,
        "username": "bob",
        "renamed_self": False,
    }
    with pytest.raises(HTTPException) as missing_rename:
        asyncio.run(rename("missing", auth_routes.RenameUserRequest(username="new"), RequestLike(token="tok-admin")))
    assert missing_rename.value.status_code == 404
    with pytest.raises(HTTPException) as conflict_rename:
        asyncio.run(rename("bob", auth_routes.RenameUserRequest(username="alice"), RequestLike(token="tok-admin")))
    assert conflict_rename.value.status_code == 409

    class FailingQuery:
        def filter(self, *args):
            return self

        def update(self, *args, **kwargs):
            raise RuntimeError("rename rows failed")

    class FailingDB:
        def query(self, model):
            return FailingQuery()

        def commit(self):
            pass

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    class OwnerColumn:
        def __eq__(self, other):
            return ("owner", other)

    class OwnedModel:
        owner = OwnerColumn()

    class UnownedModel:
        pass

    failing_core_db = types.ModuleType("core.database")
    failing_core_db.Base = SimpleNamespace(registry=SimpleNamespace(mappers=[SimpleNamespace(class_=OwnedModel)]))
    failing_core_db.SessionLocal = lambda: FailingDB()
    monkeypatch.setitem(sys.modules, "core.database", failing_core_db)
    with pytest.raises(HTTPException) as rename_db_failed:
        asyncio.run(rename("bob", auth_routes.RenameUserRequest(username="robert"), RequestLike(token="tok-admin")))
    assert rename_db_failed.value.status_code == 500

    class Query:
        def filter(self, *args):
            return self

        def update(self, *args, **kwargs):
            return 1

    class DB:
        def query(self, model):
            return Query()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    core_db = types.ModuleType("core.database")
    core_db.Base = SimpleNamespace(registry=SimpleNamespace(mappers=[SimpleNamespace(class_=UnownedModel), SimpleNamespace(class_=OwnedModel)]))
    core_db.SessionLocal = lambda: DB()
    monkeypatch.setitem(sys.modules, "core.database", core_db)
    prefs = {"_users": {"bob": {"theme": "dark"}}}
    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load = lambda: (_ for _ in ()).throw(RuntimeError("prefs failed"))
    prefs_mod._save = lambda value: prefs.update(value)
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)
    assert asyncio.run(rename("bob", auth_routes.RenameUserRequest(username="bobby"), RequestLike(token="tok-admin"))) == {
        "ok": True,
        "username": "bobby",
        "renamed_self": False,
    }
    auth.rename_user = lambda old, new, requesting_user: False
    with pytest.raises(HTTPException) as auth_rename_failed:
        asyncio.run(rename("bobby", auth_routes.RenameUserRequest(username="robert"), RequestLike(token="tok-admin")))
    assert auth_rename_failed.value.status_code == 400
    auth.rename_user = lambda old, new, requesting_user: FakeAuth.rename_user(auth, old, new, requesting_user)
    prefs = {"_users": {"bobby": {"theme": "dark"}}}
    prefs_mod._load = lambda: prefs
    prefs_mod._save = lambda value: prefs.update(value)
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)
    assert asyncio.run(rename("bobby", auth_routes.RenameUserRequest(username="robert"), RequestLike(token="tok-admin"))) == {
        "ok": True,
        "username": "robert",
        "renamed_self": False,
    }
    assert "robert" in auth.users
    assert "robert" in prefs["_users"]

    toggle = _endpoint(router, "/api/auth/signup-toggle", "POST")
    with pytest.raises(HTTPException) as denied_toggle:
        asyncio.run(toggle(RequestLike(token="tok-alice")))
    assert denied_toggle.value.status_code == 403
    assert asyncio.run(toggle(RequestLike(token="tok-admin"))) == {"ok": True, "signup_enabled": True}
    delete = _endpoint(router, "/api/auth/users", "DELETE")
    with pytest.raises(HTTPException) as denied_delete:
        asyncio.run(delete(auth_routes.DeleteUserRequest(username="alice"), RequestLike(token="tok-alice")))
    assert denied_delete.value.status_code == 403
    with pytest.raises(HTTPException) as delete_self:
        asyncio.run(delete(auth_routes.DeleteUserRequest(username="admin"), RequestLike(token="tok-admin")))
    assert delete_self.value.status_code == 400
    assert asyncio.run(delete(auth_routes.DeleteUserRequest(username="robert"), RequestLike(token="tok-admin"))) == {"ok": True}

    features = {"email": False, "code": True}
    saved_features = []
    monkeypatch.setattr(auth_routes, "_load_features", lambda: dict(features))
    monkeypatch.setattr(auth_routes, "_save_features", lambda value: saved_features.append(dict(value)))
    assert asyncio.run(_endpoint(router, "/api/auth/features")()) == features
    with pytest.raises(HTTPException) as denied_features:
        asyncio.run(_endpoint(router, "/api/auth/features", "POST")(RequestLike(token="tok-alice", body={"email": True})))
    assert denied_features.value.status_code == 403
    assert asyncio.run(_endpoint(router, "/api/auth/features", "POST")(RequestLike(token="tok-admin", body={"email": True, "bad": True}))) == {
        "email": True,
        "code": True,
    }
    assert saved_features[-1]["email"] is True
    monkeypatch.setattr(
        auth_routes,
        "_load_features",
        lambda: (_ for _ in ()).throw(RuntimeError("features unavailable")),
    )
    failed_features = asyncio.run(_endpoint(router, "/api/auth/features")())
    assert failed_features["email"] is False
    assert failed_features["web_search"] is False
    assert failed_features["external_model_endpoints"] is False
    assert failed_features["document_editor"] is True
    monkeypatch.setattr(auth_routes, "_load_features", lambda: {"network_integrations": True, "network_notifications": True})

    settings = {"api_key": "secret", "theme": "dark"}
    saved_settings = []
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: dict(settings))
    monkeypatch.setattr(auth_routes, "DEFAULT_SETTINGS", {"api_key": "", "theme": "light"})
    monkeypatch.setattr(auth_routes, "scrub_settings", lambda value: {"api_key": "", "theme": value["theme"]})
    monkeypatch.setattr(auth_routes, "_save_settings", lambda value: saved_settings.append(dict(value)))
    assert asyncio.run(_endpoint(router, "/api/auth/settings")(RequestLike(token="tok-alice"))) == {"api_key": "", "theme": "dark"}
    assert asyncio.run(_endpoint(router, "/api/auth/settings")(RequestLike(token="tok-admin"))) == settings
    with pytest.raises(HTTPException) as denied_settings:
        asyncio.run(_endpoint(router, "/api/auth/settings", "POST")(RequestLike(token="tok-alice", body={"theme": "light"})))
    assert denied_settings.value.status_code == 403
    assert asyncio.run(_endpoint(router, "/api/auth/settings", "POST")(RequestLike(token="tok-admin", body={"theme": "light", "ignored": "x"})))["theme"] == "light"
    assert saved_settings[-1] == {"api_key": "secret", "theme": "light"}

    integrations = [{"id": "i1", "name": "Miniflux", "api_key": "secret", "preset": "miniflux"}]
    monkeypatch.setattr(auth_routes, "offline_mode", lambda: True)
    with pytest.raises(HTTPException) as denied_integrations:
        asyncio.run(_endpoint(router, "/api/auth/integrations")(RequestLike(token="tok-alice")))
    assert denied_integrations.value.status_code == 403
    assert asyncio.run(_endpoint(router, "/api/auth/integrations")(RequestLike(token="tok-admin"))) == {"integrations": []}
    assert asyncio.run(_endpoint(router, "/api/auth/integrations/presets")()) == {"presets": {}}
    with pytest.raises(HTTPException) as offline_create:
        asyncio.run(_endpoint(router, "/api/auth/integrations", "POST")(RequestLike(token="tok-admin", body={"name": "x"})))
    assert offline_create.value.status_code == 403

    monkeypatch.setattr(auth_routes, "offline_mode", lambda: False)
    monkeypatch.setattr(auth_routes, "load_integrations", lambda: list(integrations))
    monkeypatch.setattr(auth_routes, "mask_integration_secret", lambda item: {**item, "api_key": "***"})
    monkeypatch.setattr(auth_routes, "INTEGRATION_PRESETS", {"miniflux": {"base_url": "http://x", "api_key": "hidden"}})
    monkeypatch.setattr(auth_routes, "add_integration", lambda body: {**body, "id": "new", "api_key": "secret"})
    monkeypatch.setattr(auth_routes, "update_integration", lambda iid, body: ({**body, "id": iid, "api_key": "secret"} if iid == "i1" else None))
    monkeypatch.setattr(auth_routes, "delete_integration", lambda iid: iid == "i1")
    monkeypatch.setattr(auth_routes, "get_integration", lambda iid: integrations[0] if iid == "i1" else None)
    monkeypatch.setattr(auth_routes, "execute_api_call", lambda *args, **kwargs: asyncio.sleep(0, result={"exit_code": 0}))
    assert asyncio.run(_endpoint(router, "/api/auth/integrations")(RequestLike(token="tok-admin")))["integrations"][0]["api_key"] == "***"
    assert "api_key" not in asyncio.run(_endpoint(router, "/api/auth/integrations/presets")())["presets"]["miniflux"]
    monkeypatch.setattr(auth_routes, "_load_features", lambda: {"network_integrations": False, "network_notifications": True})
    assert asyncio.run(_endpoint(router, "/api/auth/integrations")(RequestLike(token="tok-admin"))) == {"integrations": []}
    assert asyncio.run(_endpoint(router, "/api/auth/integrations/presets")()) == {"presets": {}}
    with pytest.raises(HTTPException) as disabled_create_integration:
        asyncio.run(_endpoint(router, "/api/auth/integrations", "POST")(RequestLike(token="tok-admin", body={"name": "New"})))
    assert disabled_create_integration.value.status_code == 403
    with pytest.raises(HTTPException) as disabled_test_integration:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("i1", RequestLike(token="tok-admin")))
    assert disabled_test_integration.value.status_code == 403
    monkeypatch.setattr(auth_routes, "_load_features", lambda: {"network_integrations": True, "network_notifications": True})
    with pytest.raises(HTTPException) as denied_create_integration:
        asyncio.run(_endpoint(router, "/api/auth/integrations", "POST")(RequestLike(token="tok-alice", body={"name": "New"})))
    assert denied_create_integration.value.status_code == 403
    assert asyncio.run(_endpoint(router, "/api/auth/integrations", "POST")(RequestLike(token="tok-admin", body={"name": "New"})))["integration"]["api_key"] == "***"
    with pytest.raises(HTTPException) as denied_update_integration:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "PUT")("i1", RequestLike(token="tok-alice", body={"name": "Updated"})))
    assert denied_update_integration.value.status_code == 403
    monkeypatch.setattr(auth_routes, "offline_mode", lambda: True)
    with pytest.raises(HTTPException) as offline_update:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "PUT")("i1", RequestLike(token="tok-admin", body={"name": "Updated"})))
    assert offline_update.value.status_code == 403
    monkeypatch.setattr(auth_routes, "offline_mode", lambda: False)
    assert asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "PUT")("i1", RequestLike(token="tok-admin", body={"name": "Updated"})))["ok"] is True
    with pytest.raises(HTTPException) as missing_update:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "PUT")("missing", RequestLike(token="tok-admin", body={})))
    assert missing_update.value.status_code == 404
    with pytest.raises(HTTPException) as denied_delete_integration:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "DELETE")("i1", RequestLike(token="tok-alice")))
    assert denied_delete_integration.value.status_code == 403
    assert asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "DELETE")("i1", RequestLike(token="tok-admin"))) == {"ok": True}
    with pytest.raises(HTTPException) as missing_delete:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}", "DELETE")("missing", RequestLike(token="tok-admin")))
    assert missing_delete.value.status_code == 404
    assert asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("i1", RequestLike(token="tok-admin"))) == {
        "ok": True,
        "message": "Connection successful",
    }
    monkeypatch.setattr(auth_routes, "execute_api_call", lambda *args, **kwargs: asyncio.sleep(0, result={"exit_code": 1, "error": "no route"}))
    failed_health = asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("i1", RequestLike(token="tok-admin")))
    assert failed_health == {"ok": False, "message": "no route"}
    with pytest.raises(HTTPException) as denied_test_integration:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("i1", RequestLike(token="tok-alice")))
    assert denied_test_integration.value.status_code == 403
    monkeypatch.setattr(auth_routes, "offline_mode", lambda: True)
    with pytest.raises(HTTPException) as offline_test:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("i1", RequestLike(token="tok-admin")))
    assert offline_test.value.status_code == 403
    monkeypatch.setattr(auth_routes, "offline_mode", lambda: False)
    with pytest.raises(HTTPException) as missing_test:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("missing", RequestLike(token="tok-admin")))
    assert missing_test.value.status_code == 404

    ntfy = {
        "id": "ntfy",
        "name": "ntfy",
        "preset": "ntfy",
        "base_url": "https://ntfy.example.test/ignored",
        "api_key": "secret",
        "auth_type": "bearer",
    }
    monkeypatch.setattr(auth_routes, "get_integration", lambda iid: ntfy if iid == "ntfy" else None)
    monkeypatch.setattr(auth_routes, "_load_settings", lambda: {"reminder_ntfy_topic": "alerts"})

    class NtfyResponse:
        is_success = True
        status_code = 200
        text = "ok"

    class NtfyClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, full_url, content=None, headers=None):
            assert full_url == "https://ntfy.example.test/alerts"
            assert headers["Authorization"] == "Bearer secret"
            return NtfyResponse()

    monkeypatch.setattr(httpx, "AsyncClient", NtfyClient)
    ntfy_success = asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("ntfy", RequestLike(token="tok-admin")))
    assert ntfy_success["ok"] is True
    assert "https://ntfy.example.test/alerts" in ntfy_success["message"]

    monkeypatch.setattr(auth_routes, "_load_features", lambda: {"network_integrations": True, "network_notifications": False})
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("disabled notifications must not open HTTP clients")),
    )
    with pytest.raises(HTTPException) as disabled_ntfy:
        asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("ntfy", RequestLike(token="tok-admin")))
    assert disabled_ntfy.value.status_code == 403
    monkeypatch.setattr(auth_routes, "_load_features", lambda: {"network_integrations": True, "network_notifications": True})

    class NtfyFailureResponse(NtfyResponse):
        is_success = False
        status_code = 500
        text = "server failed"

    class NtfyFailureClient(NtfyClient):
        async def post(self, full_url, content=None, headers=None):
            return NtfyFailureResponse()

    monkeypatch.setattr(httpx, "AsyncClient", NtfyFailureClient)
    ntfy_failure = asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("ntfy", RequestLike(token="tok-admin")))
    assert ntfy_failure == {"ok": False, "message": "ntfy returned HTTP 500 from https://ntfy.example.test/alerts: server failed"}

    ntfy["auth_type"] = "header"
    ntfy["auth_header"] = "X-Token"
    ntfy["base_url"] = "http://127.0.0.1:8091"

    class NtfyExceptionClient(NtfyClient):
        async def post(self, full_url, content=None, headers=None):
            assert headers["X-Token"] == "secret"
            raise RuntimeError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", NtfyExceptionClient)
    ntfy_exception = asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("ntfy", RequestLike(token="tok-admin")))
    assert ntfy_exception["ok"] is False
    assert "connection refused" in ntfy_exception["message"]

    ntfy["base_url"] = "https://ntfy.example.test"
    hinted_exception = asyncio.run(_endpoint(router, "/api/auth/integrations/{integration_id}/test", "POST")("ntfy", RequestLike(token="tok-admin")))
    assert "NTFY_BIND" in hinted_exception["message"]
