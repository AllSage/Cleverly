import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


def _fresh_contacts():
    sys.modules.pop("routes.contacts_routes", None)
    return importlib.import_module("routes.contacts_routes")


def test_contacts_local_helpers_and_routes(monkeypatch, tmp_path):
    contacts = _fresh_contacts()
    monkeypatch.setattr(contacts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(contacts, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(contacts, "LOCAL_CONTACTS_FILE", tmp_path / "contacts.json")
    monkeypatch.setattr(contacts, "offline_mode", lambda: False)
    monkeypatch.setattr(contacts.uuid, "uuid4", lambda: "uid-generated")
    monkeypatch.delenv("CARDDAV_URL", raising=False)
    monkeypatch.delenv("CARDDAV_USERNAME", raising=False)
    monkeypatch.delenv("CARDDAV_PASSWORD", raising=False)
    contacts._contact_cache.update({"contacts": [], "fetched_at": None})

    assert contacts._load_settings() == {}
    contacts._save_settings({"carddav_password": "secret"})
    assert contacts._load_settings()["carddav_password"] == "secret"
    monkeypatch.setattr(contacts, "offline_mode", lambda: True)
    assert contacts._get_carddav_config() == {"url": "", "username": "", "password": ""}
    monkeypatch.setattr(contacts, "offline_mode", lambda: False)

    normalized = contacts._normalize_contact({"email": " a@example.com ", "emails": ["a@example.com", "b@example.com"], "phone": " 123 "})
    assert normalized["name"] == "a"
    assert normalized["emails"] == ["a@example.com", "b@example.com"]
    assert normalized["phones"] == ["123"]
    assert contacts._vunesc(r"Alice\, Inc\; Team\nLine") == "Alice, Inc; Team\nLine"
    assert contacts._vesc("A,B;C\\D\nE") == r"A\,B\;C\\D\nE"

    vcard = contacts._build_vcard(
        "Alice Example",
        "alice@example.com",
        uid="alice-uid",
        emails=["alice@example.com", "alice2@example.com"],
        phones=["555"],
    )
    parsed = contacts._parse_vcards(vcard)
    assert parsed == [{"name": "Alice Example", "emails": ["alice@example.com", "alice2@example.com"], "phones": ["555"], "uid": "alice-uid"}]

    assert contacts._load_local_contacts() == []
    contacts._save_local_contacts(parsed)
    assert contacts._load_local_contacts()[0]["uid"] == "alice-uid"
    contacts.LOCAL_CONTACTS_FILE.write_text("{bad json", encoding="utf-8")
    assert contacts._load_local_contacts() == []
    contacts._save_local_contacts(parsed)

    first_fetch = contacts._fetch_contacts(force=True)
    assert first_fetch[0]["name"] == "Alice Example"
    assert contacts._fetch_contacts() == first_fetch

    assert contacts._create_contact("Alice Duplicate", "alice@example.com") is True
    assert len(contacts._load_local_contacts()) == 1
    assert contacts._create_contact("", "bob@example.com") is True
    assert any("bob@example.com" in c["emails"] for c in contacts._load_local_contacts())

    imported_vcf = contacts._import_vcards(
        contacts._build_vcard("Cara Contact", "cara@example.com", uid="cara")
        + contacts._build_vcard("Alice Duplicate", "alice@example.com", uid="dup")
    )
    assert imported_vcf == {"imported": 1, "failed": 0, "total": 2}

    csv_result = contacts._import_csv_contacts("name,email,phone\nDana,dana@example.com,777\nNo Email,,123\n")
    assert csv_result["imported"] == 1
    assert csv_result["total"] == 1
    assert contacts._import_csv_contacts("")["error"] == "No CSV data found"

    csv_export = contacts._contacts_to_csv(contacts._fetch_contacts(force=True))
    assert "name,email,phone" in csv_export
    assert "dana@example.com" in csv_export
    assert "BEGIN:VCARD" in contacts._contacts_to_vcf(contacts._fetch_contacts())

    assert contacts._update_contact("alice-uid", "Alice Updated", ["alice.new@example.com"], ["999"]) is True
    assert contacts._delete_contact("cara") is True

    router = contacts.setup_contacts_routes()
    listed = asyncio.run(_endpoint(router, "/api/contacts/list")(_admin=None))
    assert listed["count"] >= 2
    assert asyncio.run(_endpoint(router, "/api/contacts/search")(q="", _admin=None)) == {"results": []}
    assert asyncio.run(_endpoint(router, "/api/contacts/search")(q="alice", _admin=None))["results"][0]["name"] == "Alice Updated"

    assert asyncio.run(_endpoint(router, "/api/contacts/add", "POST")({"email": ""}, _admin=None)) == {
        "success": False,
        "error": "Email required",
    }
    existing = asyncio.run(_endpoint(router, "/api/contacts/add", "POST")({"email": "alice.new@example.com"}, _admin=None))
    assert existing["message"] == "Already exists"
    assert asyncio.run(_endpoint(router, "/api/contacts/add", "POST")({"email": "eve@example.com"}, _admin=None)) == {"success": True}

    assert asyncio.run(_endpoint(router, "/api/contacts/import", "POST")({}, _admin=None)) == {
        "success": False,
        "error": "No contact data found",
    }
    assert asyncio.run(_endpoint(router, "/api/contacts/import", "POST")({"vcf": "not a card"}, _admin=None)) == {
        "success": False,
        "error": "No vCard data found",
    }
    imported = asyncio.run(
        _endpoint(router, "/api/contacts/import", "POST")(
            {"vcf": contacts._build_vcard("Frank", "frank@example.com", uid="frank")},
            _admin=None,
        )
    )
    assert imported["success"] is True
    csv_imported = asyncio.run(
        _endpoint(router, "/api/contacts/import", "POST")({"csv": "name,email\nGrace,grace@example.com\n"}, _admin=None)
    )
    assert csv_imported["success"] is True

    exported_csv = asyncio.run(_endpoint(router, "/api/contacts/export")(format="csv", _admin=None))
    assert exported_csv.media_type == "text/csv; charset=utf-8"
    assert b"grace@example.com" in exported_csv.body
    exported_vcf = asyncio.run(_endpoint(router, "/api/contacts/export")(format="vcf", _admin=None))
    assert exported_vcf.media_type == "text/vcard; charset=utf-8"

    contacts._save_settings({"carddav_url": "https://dav.example/book", "carddav_username": "u", "carddav_password": "p"})
    cfg = asyncio.run(_endpoint(router, "/api/contacts/config")(_admin=None))
    assert cfg["password"] == "***"
    monkeypatch.setattr(contacts, "offline_mode", lambda: True)
    with pytest.raises(HTTPException) as offline_cfg:
        asyncio.run(_endpoint(router, "/api/contacts/config", "PUT")({"carddav_url": "https://dav.example/book"}, _admin=None))
    assert offline_cfg.value.status_code == 403
    monkeypatch.setattr(contacts, "offline_mode", lambda: False)
    assert asyncio.run(_endpoint(router, "/api/contacts/config", "PUT")({"carddav_username": "new"}, _admin=None)) == {"success": True}
    assert asyncio.run(_endpoint(router, "/api/contacts/config", "PUT")({"carddav_url": ""}, _admin=None)) == {"success": True}

    assert asyncio.run(_endpoint(router, "/api/contacts/{uid}", "PUT")("new-local", {"name": "", "emails": []}, _admin=None)) == {
        "success": False,
        "error": "Name or email required",
    }
    assert asyncio.run(_endpoint(router, "/api/contacts/{uid}", "PUT")("new-local", {"email": "local@example.com"}, _admin=None)) == {
        "success": True
    }
    assert asyncio.run(_endpoint(router, "/api/contacts/{uid}", "DELETE")("", _admin=None)) == {
        "success": False,
        "error": "UID required",
    }
    assert asyncio.run(_endpoint(router, "/api/contacts/{uid}", "DELETE")("new-local", _admin=None)) == {"success": True}
    assert asyncio.run(_endpoint(router, "/api/contacts/clear", "DELETE")(_admin=None)) == {"success": True}


def test_contacts_carddav_helpers_success_failures_and_import(monkeypatch):
    contacts = _fresh_contacts()
    cfg = {"url": "https://dav.example/addressbook", "username": "user", "password": "pass"}
    monkeypatch.setattr(contacts, "_get_carddav_config", lambda: dict(cfg))
    contacts._contact_cache.update({"contacts": [], "fetched_at": None})

    class Resp:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text

    report_xml = """<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
<D:response>
<D:href>/addressbook/remote.vcf</D:href>
<D:propstat><D:prop><C:address-data>BEGIN:VCARD
VERSION:4.0
UID:remote
FN:Remote User
EMAIL:remote@example.com
END:VCARD</C:address-data></D:prop></D:propstat>
</D:response>
</D:multistatus>"""

    request_calls = []

    def fake_request(method, url, **kwargs):
        request_calls.append((method, url, kwargs))
        return Resp(207, report_xml)

    monkeypatch.setattr(contacts.httpx, "request", fake_request)
    reported = contacts._fetch_via_report(cfg, ("user", "pass"))
    assert reported[0]["href"] == "/addressbook/remote.vcf"
    assert request_calls[0][0] == "REPORT"

    monkeypatch.setattr(contacts.httpx, "request", lambda *args, **kwargs: Resp(500, "bad"))
    assert contacts._fetch_via_report(cfg, None) is None
    monkeypatch.setattr(contacts.httpx, "request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert contacts._fetch_via_report(cfg, None) is None

    monkeypatch.setattr(contacts, "_fetch_via_report", lambda cfg, auth: [{"uid": "r1", "name": "R", "emails": ["r@example.com"], "phones": [], "href": "/addressbook/r1.vcf"}])
    assert contacts._fetch_contacts(force=True)[0]["uid"] == "r1"
    assert contacts._abs_url("/addressbook/r1.vcf") == "https://dav.example/addressbook/r1.vcf"
    assert contacts._abs_url("https://other.example/x.vcf") == "https://other.example/x.vcf"
    assert contacts._resolve_resource_url("r1") == "https://dav.example/addressbook/r1.vcf"

    monkeypatch.setattr(contacts, "_fetch_via_report", lambda cfg, auth: None)
    monkeypatch.setattr(
        contacts.httpx,
        "get",
        lambda url, auth=None, timeout=10: Resp(
            200,
            contacts._build_vcard("Get User", "get@example.com", uid="get-uid"),
        ),
    )
    fetched = contacts._fetch_contacts(force=True)
    assert fetched[0]["uid"] == "get-uid"
    monkeypatch.setattr(contacts.httpx, "get", lambda *args, **kwargs: Resp(503, "unavailable"))
    assert contacts._fetch_contacts(force=True) == fetched
    monkeypatch.setattr(contacts.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    assert contacts._fetch_contacts(force=True) == fetched

    put_statuses = []

    def fake_put(url, data=None, headers=None, auth=None, timeout=None):
        put_statuses.append((url, data, headers, auth, timeout))
        return Resp(put_statuses[-1][4] if isinstance(put_statuses[-1][4], int) else 201, "ok")

    monkeypatch.setattr(contacts.uuid, "uuid4", lambda: "new/uid")
    monkeypatch.setattr(contacts.httpx, "put", lambda *args, **kwargs: Resp(201, "created"))
    assert contacts._create_contact("Remote New", "remote.new@example.com") is True
    monkeypatch.setattr(contacts.httpx, "put", lambda *args, **kwargs: Resp(500, "failed"))
    assert contacts._create_contact("Remote New", "remote.new@example.com") is False
    monkeypatch.setattr(contacts.httpx, "put", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("put down")))
    assert contacts._create_contact("Remote New", "remote.new@example.com") is False

    assert contacts._vcard_url("a/b uid") == "https://dav.example/addressbook/a%2Fb%20uid.vcf"

    statuses = [Resp(201, "ok"), Resp(500, "bad")]

    def sequenced_put(*args, **kwargs):
        return statuses.pop(0)

    monkeypatch.setattr(contacts.httpx, "put", sequenced_put)
    imported = contacts._import_vcards(
        "BEGIN:VCARD\nFN:No Version\nEMAIL:no-version@example.com\nEND:VCARD\n"
        "BEGIN:VCARD\nVERSION:4.0\nUID:kept\nFN:Kept\nEMAIL:kept@example.com\nEND:VCARD"
    )
    assert imported == {"imported": 1, "failed": 1, "total": 2}

    monkeypatch.setattr(contacts.httpx, "put", lambda *args, **kwargs: Resp(204, "ok"))
    assert contacts._update_contact("r1", "Remote Updated", ["r@example.com"], ["555"]) is True
    monkeypatch.setattr(contacts.httpx, "put", lambda *args, **kwargs: Resp(500, "bad"))
    assert contacts._update_contact("r1", "Remote Updated", ["r@example.com"], ["555"]) is False
    monkeypatch.setattr(contacts.httpx, "put", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert contacts._update_contact("r1", "Remote Updated", ["r@example.com"], ["555"]) is False

    monkeypatch.setattr(contacts.httpx, "delete", lambda *args, **kwargs: Resp(204, "ok"))
    assert contacts._delete_contact("r1") is True
    monkeypatch.setattr(contacts.httpx, "delete", lambda *args, **kwargs: Resp(404, "gone"))
    assert contacts._delete_contact("r1") is True
    monkeypatch.setattr(contacts.httpx, "delete", lambda *args, **kwargs: Resp(500, "bad"))
    assert contacts._delete_contact("r1") is False
    monkeypatch.setattr(contacts.httpx, "delete", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))
    assert contacts._delete_contact("r1") is False
