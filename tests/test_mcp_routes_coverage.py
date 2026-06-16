import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse


def _endpoint(router, path: str, method: str | None = None):
    method = method.upper() if method else None
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and (method is None or method in getattr(route, "methods", set()))
    )


class RequestLike:
    def __init__(self, user="alice", host="localhost:7000", body=None):
        self.state = SimpleNamespace(current_user=user)
        self.headers = {"host": host}
        self._body = body if body is not None else {}

    async def json(self):
        return self._body


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, "eq", other)


class FakeMcpServer:
    id = Field("id")

    def __init__(self, **kwargs):
        self.disabled_tools = None
        self.oauth_config = None
        self.is_enabled = True
        self.args = "[]"
        self.env = "{}"
        self.url = None
        self.command = None
        self.transport = "stdio"
        self.name = ""
        self.__dict__.update(kwargs)


class Query:
    def __init__(self, db):
        self.db = db
        self.server_id = None

    def filter(self, expr):
        if isinstance(expr, tuple) and expr[0] == "id":
            self.server_id = expr[2]
        return self

    def all(self):
        return list(self.db.servers.values())

    def first(self):
        if self.server_id is None:
            return None
        return self.db.servers.get(self.server_id)


class FakeDB:
    def __init__(self, servers=None):
        self.servers = {srv.id: srv for srv in (servers or [])}
        self.added = []
        self.deleted = []
        self.commits = 0
        self.closed = 0

    def query(self, model):
        return Query(self)

    def add(self, obj):
        self.servers[obj.id] = obj
        self.added.append(obj)

    def delete(self, obj):
        self.servers.pop(obj.id, None)
        self.deleted.append(obj.id)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed += 1


class FakeManager:
    def __init__(self):
        self.connected = []
        self.disconnected = []
        self.statuses = {}
        self.tools = [
            {"server_id": "srv1", "name": "read_mail"},
            {"server_id": "srv1", "name": "send_mail"},
            {"server_id": "other", "name": "other_tool"},
        ]

    def get_server_status(self, server_id):
        return self.statuses.get(server_id, {"status": "connected", "tool_count": 2, "error": None})

    async def connect_server(self, **kwargs):
        self.connected.append(kwargs)
        return kwargs.get("server_id") != "fail"

    async def disconnect_server(self, server_id):
        self.disconnected.append(server_id)

    def get_all_tools(self, disabled_map=None):
        if disabled_map:
            return [
                {**tool, "disabled": tool["name"] in disabled_map.get(tool["server_id"], set())}
                for tool in self.tools
            ]
        return [dict(tool) for tool in self.tools]


def _fresh_routes(monkeypatch, db, *, offline=False):
    import routes.mcp_routes as mcp_routes

    mcp_routes = importlib.reload(mcp_routes)
    monkeypatch.setattr(mcp_routes, "McpServer", FakeMcpServer)
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(mcp_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(mcp_routes, "offline_mode", lambda: offline)
    return mcp_routes


def test_mcp_server_crud_tools_and_offline_gates(monkeypatch, tmp_path):
    oauth_keys = tmp_path / "keys.json"
    oauth_token = tmp_path / "token.json"
    oauth_keys.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}), encoding="utf-8")
    servers = [
        FakeMcpServer(
            id="srv1",
            name="Mail",
            transport="stdio",
            command="mailcmd",
            args=json.dumps(["--one"]),
            env=json.dumps({"A": "B"}),
            disabled_tools=json.dumps(["send_mail"]),
            oauth_config=json.dumps({"token_file": str(oauth_token), "keys_file": str(oauth_keys), "scopes": ["mail"]}),
        ),
        FakeMcpServer(id="badjson", name="Bad", disabled_tools="{"),
    ]
    db = FakeDB(servers)
    mcp_routes = _fresh_routes(monkeypatch, db)
    manager = FakeManager()
    manager.statuses["srv1"] = {"status": "connected", "tool_count": 3, "error": None}
    router = mcp_routes.setup_mcp_routes(manager)
    request = RequestLike()

    assert mcp_routes._load_disabled_map() == {"srv1": {"send_mail"}}
    db.servers.pop("badjson")

    listed = _endpoint(router, "/api/mcp/servers")(request)
    assert listed[0]["id"] == "srv1"
    assert listed[0]["needs_oauth"] is True
    assert listed[0]["enabled_tool_count"] == 2

    assert _endpoint(router, "/api/mcp/tools")(request)[0]["disabled"] is False
    srv_tools = _endpoint(router, "/api/mcp/servers/{server_id}/tools")(server_id="srv1", request=request)
    assert {tool["name"]: tool["is_disabled"] for tool in srv_tools} == {
        "read_mail": False,
        "send_mail": True,
    }

    updated = asyncio.run(
        _endpoint(router, "/api/mcp/servers/{server_id}/tools", "PATCH")(
            "srv1",
            RequestLike(body={"disabled": ["read_mail"]}),
        )
    )
    assert updated == {"id": "srv1", "disabled_count": 1}
    assert json.loads(db.servers["srv1"].disabled_tools) == ["read_mail"]

    with pytest.raises(HTTPException) as bad_disabled:
        asyncio.run(
            _endpoint(router, "/api/mcp/servers/{server_id}/tools", "PATCH")(
                "srv1",
                RequestLike(body={"disabled": "bad"}),
            )
        )
    assert bad_disabled.value.status_code == 400

    with pytest.raises(HTTPException) as missing_tools:
        _endpoint(router, "/api/mcp/servers/{server_id}/tools")("missing", request)
    assert missing_tools.value.status_code == 404

    monkeypatch.setattr(mcp_routes.uuid, "uuid4", lambda: "newserverid")
    oauth_dir = tmp_path / "oauth"
    added = asyncio.run(
        _endpoint(router, "/api/mcp/servers", "POST")(
            request,
            name="New",
            transport="stdio",
            command="cmd",
            args="{bad",
            env=json.dumps({"GOOGLE_CLIENT_ID": "remove", "KEEP": "yes"}),
            oauth_file=json.dumps(
                {
                    "dir": str(oauth_dir),
                    "filename": "client.json",
                    "client_id": "client",
                    "client_secret": "secret",
                }
            ),
            oauth_config=json.dumps({"token_file": str(oauth_token)}),
        )
    )
    assert added["id"] == "newserve"
    assert added["needs_oauth"] is True
    written_oauth = json.loads((oauth_dir / "client.json").read_text(encoding="utf-8"))
    assert written_oauth["installed"]["client_id"] == "client"
    assert json.loads(db.servers["newserve"].env) == {"KEEP": "yes"}
    assert manager.connected == []

    oauth_token.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp_routes.uuid, "uuid4", lambda: "connectid")
    added_connected = asyncio.run(
        _endpoint(router, "/api/mcp/servers", "POST")(
            request,
            name="Connected",
            transport="sse",
            command=None,
            url="http://example.test/sse",
            args="[]",
            env="{}",
            oauth_file=None,
            oauth_config=json.dumps({"token_file": str(oauth_token)}),
        )
    )
    assert added_connected["connected"] is True
    assert manager.connected[-1]["server_id"] == "connecti"

    monkeypatch.setattr(mcp_routes.uuid, "uuid4", lambda: "invalidjsonid")
    invalid_json_added = asyncio.run(
        _endpoint(router, "/api/mcp/servers", "POST")(
            request,
            name="Invalid Json",
            transport="stdio",
            command="cmd",
            args="[]",
            env="{bad",
            oauth_file="{bad",
            oauth_config="{bad",
        )
    )
    assert invalid_json_added["id"] == "invalidj"
    assert json.loads(db.servers["invalidj"].env) == {}
    assert db.servers["invalidj"].oauth_config is None

    with pytest.raises(HTTPException) as no_command:
        asyncio.run(_endpoint(router, "/api/mcp/servers", "POST")(request, name="x", transport="stdio", command=None))
    assert no_command.value.status_code == 400
    with pytest.raises(HTTPException) as no_url:
        asyncio.run(_endpoint(router, "/api/mcp/servers", "POST")(request, name="x", transport="sse", url=None))
    assert no_url.value.status_code == 400

    reconnected = asyncio.run(_endpoint(router, "/api/mcp/servers/{server_id}/reconnect", "POST")("srv1", request))
    assert reconnected["connected"] is True
    assert manager.disconnected[-1] == "srv1"

    disabled = asyncio.run(_endpoint(router, "/api/mcp/servers/{server_id}", "PATCH")("srv1", request, is_enabled="false"))
    assert disabled == {"id": "srv1", "is_enabled": False}
    assert manager.disconnected[-1] == "srv1"
    enabled = asyncio.run(_endpoint(router, "/api/mcp/servers/{server_id}", "PATCH")("srv1", request, is_enabled="true"))
    assert enabled == {"id": "srv1", "is_enabled": True}
    assert manager.connected[-1]["server_id"] == "srv1"

    deleted = asyncio.run(_endpoint(router, "/api/mcp/servers/{server_id}", "DELETE")("srv1", request))
    assert deleted == {"status": "deleted"}
    assert "srv1" not in db.servers

    for path, method, args in [
        ("/api/mcp/servers/{server_id}/reconnect", "POST", ("missing", request)),
        ("/api/mcp/servers/{server_id}", "PATCH", ("missing", request)),
        ("/api/mcp/servers/{server_id}", "DELETE", ("missing", request)),
        ("/api/mcp/servers/{server_id}/tools", "PATCH", ("missing", request)),
    ]:
        with pytest.raises(HTTPException) as exc:
            endpoint = _endpoint(router, path, method)
            if path.endswith("/tools"):
                asyncio.run(endpoint(*args))
            elif method == "PATCH":
                asyncio.run(endpoint(*args, is_enabled="false"))
            else:
                asyncio.run(endpoint(*args))
        assert exc.value.status_code == 404

    offline_routes = _fresh_routes(monkeypatch, db, offline=True)
    offline_router = offline_routes.setup_mcp_routes(manager)
    assert _endpoint(offline_router, "/api/mcp/servers")(request) == []
    assert _endpoint(offline_router, "/api/mcp/tools")(request) == []
    assert _endpoint(offline_router, "/api/mcp/servers/{server_id}/tools")("anything", request) == []
    with pytest.raises(HTTPException) as offline_add:
        asyncio.run(_endpoint(offline_router, "/api/mcp/servers", "POST")(request, name="x", transport="stdio", command="cmd"))
    assert offline_add.value.status_code == 403
    with pytest.raises(HTTPException) as offline_reconnect:
        asyncio.run(_endpoint(offline_router, "/api/mcp/servers/{server_id}/reconnect", "POST")("srv", request))
    assert offline_reconnect.value.status_code == 403
    with pytest.raises(HTTPException) as offline_enable:
        asyncio.run(_endpoint(offline_router, "/api/mcp/servers/{server_id}", "PATCH")("srv", request, is_enabled="true"))
    assert offline_enable.value.status_code == 403


def test_mcp_oauth_authorize_pages_and_helpers(monkeypatch, tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps({"installed": {"client_id": "client", "client_secret": "secret"}}),
        encoding="utf-8",
    )
    srv = FakeMcpServer(
        id="srv1",
        name="Mail",
        oauth_config=json.dumps({"keys_file": str(keys_file), "token_file": str(tmp_path / "token.json"), "scopes": ["a", "b"]}),
    )
    db = FakeDB([srv])
    mcp_routes = _fresh_routes(monkeypatch, db)
    router = mcp_routes.setup_mcp_routes(FakeManager())

    redirect = _endpoint(router, "/api/mcp/oauth/authorize/{server_id}")("srv1", RequestLike(host="localhost:7000"))
    assert isinstance(redirect, RedirectResponse)
    assert "client_id=client" in redirect.headers["location"]
    assert "scope=a+b" in redirect.headers["location"]

    remote = _endpoint(router, "/api/mcp/oauth/authorize/{server_id}")(
        "srv1",
        RequestLike(host='remote.test"><script>'),
    )
    assert isinstance(remote, HTMLResponse)
    assert b"&lt;script&gt;" in remote.body
    assert b'remote.test&quot;&gt;' in remote.body

    with pytest.raises(HTTPException) as missing:
        _endpoint(router, "/api/mcp/oauth/authorize/{server_id}")("missing", RequestLike())
    assert missing.value.status_code == 404

    srv.oauth_config = None
    with pytest.raises(HTTPException) as no_oauth:
        _endpoint(router, "/api/mcp/oauth/authorize/{server_id}")("srv1", RequestLike())
    assert no_oauth.value.status_code == 400

    srv.oauth_config = json.dumps({"keys_file": str(tmp_path / "missing.json")})
    with pytest.raises(HTTPException) as no_keys:
        _endpoint(router, "/api/mcp/oauth/authorize/{server_id}")("srv1", RequestLike())
    assert no_keys.value.status_code == 400

    keys_file.write_text(json.dumps({"bad": {}}), encoding="utf-8")
    srv.oauth_config = json.dumps({"keys_file": str(keys_file)})
    with pytest.raises(HTTPException) as bad_keys:
        _endpoint(router, "/api/mcp/oauth/authorize/{server_id}")("srv1", RequestLike())
    assert bad_keys.value.status_code == 400

    auth_page = mcp_routes._oauth_authorize_page("https://x.test?a=<b>", 'srv"><id', 'host"><x')
    assert "&lt;b&gt;" in auth_page
    assert "srv&quot;&gt;" in auth_page
    result_page = mcp_routes._oauth_result_page("<Title>", "<Message>", success=True)
    assert "&lt;Title&gt;" in result_page
    assert "&#10003;" in result_page

    offline_routes = _fresh_routes(monkeypatch, db, offline=True)
    offline_router = offline_routes.setup_mcp_routes(FakeManager())
    with pytest.raises(HTTPException) as offline_oauth:
        _endpoint(offline_router, "/api/mcp/oauth/authorize/{server_id}")("srv1", RequestLike())
    assert offline_oauth.value.status_code == 403


def test_mcp_oauth_exchange_and_callback(monkeypatch, tmp_path):
    keys_file = tmp_path / "keys.json"
    token_file = tmp_path / "tokens" / "token.json"
    keys_file.write_text(
        json.dumps({"web": {"client_id": "client", "client_secret": "secret"}}),
        encoding="utf-8",
    )
    srv = FakeMcpServer(
        id="srv1",
        name="Mail",
        args=json.dumps(["--oauth"]),
        env=json.dumps({"A": "B"}),
        oauth_config=json.dumps({"keys_file": str(keys_file), "token_file": str(token_file)}),
    )
    db = FakeDB([srv])
    mcp_routes = _fresh_routes(monkeypatch, db)
    manager = FakeManager()
    router = mcp_routes.setup_mcp_routes(manager)

    class Response:
        def __init__(self, status_code=200, text="OK"):
            self.status_code = status_code
            self.text = text

        def json(self):
            return {"access_token": "tok", "refresh_token": "refresh"}

    class Client:
        response = Response()
        posts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, data=None):
            self.posts.append((url, data))
            return self.response

    monkeypatch.setattr(mcp_routes.httpx, "AsyncClient", Client)

    invalid_code = asyncio.run(
        _endpoint(router, "/api/mcp/oauth/exchange/{server_id}", "POST")(
            "srv1",
            RequestLike(),
            callback_url="http://localhost/callback?state=srv1",
        )
    )
    assert invalid_code.status_code == 400
    assert b"No authorization code" in invalid_code.body
    original_urlparse = mcp_routes.urllib.parse.urlparse
    monkeypatch.setattr(
        mcp_routes.urllib.parse,
        "urlparse",
        lambda _url: (_ for _ in ()).throw(ValueError("bad url")),
    )
    invalid_url = asyncio.run(
        _endpoint(router, "/api/mcp/oauth/exchange/{server_id}", "POST")(
            "srv1",
            RequestLike(),
            callback_url="not parsed",
        )
    )
    assert invalid_url.status_code == 400
    assert b"Invalid URL format" in invalid_url.body
    monkeypatch.setattr(mcp_routes.urllib.parse, "urlparse", original_urlparse)

    success = asyncio.run(
        _endpoint(router, "/api/mcp/oauth/exchange/{server_id}", "POST")(
            "srv1",
            RequestLike(),
            callback_url="http://localhost/callback?code=abc&state=srv1",
        )
    )
    assert isinstance(success, HTMLResponse)
    assert b"Authorization Successful" in success.body
    assert json.loads(token_file.read_text(encoding="utf-8"))["access_token"] == "tok"
    assert manager.connected[-1]["args"] == ["--oauth"]

    callback = asyncio.run(_endpoint(router, "/api/mcp/oauth/callback")("abc", "srv1", RequestLike()))
    assert b"Authorization Successful" in callback.body

    Client.response = Response(400, "bad token")
    failed = asyncio.run(
        _endpoint(router, "/api/mcp/oauth/exchange/{server_id}", "POST")(
            "srv1",
            RequestLike(),
            callback_url="http://localhost/callback?code=bad",
        )
    )
    assert failed.status_code == 400
    assert b"Authorization Failed" in failed.body

    Client.response = Response()
    manager.connect_server = lambda **kwargs: asyncio.sleep(0, result=False)
    manager.statuses["srv1"] = {"status": "error", "tool_count": 0, "error": "connect bad"}
    connection_failed = asyncio.run(
        _endpoint(router, "/api/mcp/oauth/exchange/{server_id}", "POST")(
            "srv1",
            RequestLike(),
            callback_url="http://localhost/callback?code=ok",
        )
    )
    assert b"Authorized but Connection Failed" in connection_failed.body

    missing = asyncio.run(_endpoint(router, "/api/mcp/oauth/callback")("code", "missing", RequestLike()))
    assert missing.status_code == 404
    srv.oauth_config = None
    no_config = asyncio.run(_endpoint(router, "/api/mcp/oauth/callback")("code", "srv1", RequestLike()))
    assert no_config.status_code == 400

    srv.oauth_config = json.dumps({"keys_file": str(tmp_path / "absent.json"), "token_file": str(token_file)})
    error = asyncio.run(_endpoint(router, "/api/mcp/oauth/callback")("code", "srv1", RequestLike()))
    assert error.status_code == 500

    offline_routes = _fresh_routes(monkeypatch, db, offline=True)
    offline_router = offline_routes.setup_mcp_routes(manager)
    with pytest.raises(HTTPException) as offline_callback:
        asyncio.run(_endpoint(offline_router, "/api/mcp/oauth/callback")("code", "srv1", RequestLike()))
    assert offline_callback.value.status_code == 403
    with pytest.raises(HTTPException) as offline_exchange:
        asyncio.run(
            _endpoint(offline_router, "/api/mcp/oauth/exchange/{server_id}", "POST")(
                "srv1",
                RequestLike(),
                callback_url="http://localhost/callback?code=abc",
            )
        )
    assert offline_exchange.value.status_code == 403
