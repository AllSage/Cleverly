import asyncio
import inspect

import pytest

from src import builtin_mcp


class RejectingMcpManager:
    async def connect_server(self, *args, **kwargs):
        raise AssertionError("offline MCP registration should not connect servers")


@pytest.mark.parametrize(
    ("offline", "features"),
    [
        (True, {"mcp": True, "email": True}),
        (False, {"mcp": False, "email": True}),
    ],
)
def test_register_builtin_servers_skips_when_offline_or_disabled(monkeypatch, offline, features):
    monkeypatch.setattr(builtin_mcp, "MCP_DISABLED", False)
    monkeypatch.setattr(builtin_mcp, "offline_mode", lambda: offline)
    monkeypatch.setattr(builtin_mcp, "load_features", lambda: dict(features))

    def fail_create_task(coro):
        if inspect.iscoroutine(coro):
            coro.close()
        raise AssertionError("offline MCP registration should not create startup tasks")

    monkeypatch.setattr(builtin_mcp.asyncio, "create_task", fail_create_task)

    asyncio.run(builtin_mcp.register_builtin_servers(RejectingMcpManager()))


def test_register_builtin_servers_skips_when_feature_load_fails(monkeypatch):
    monkeypatch.setattr(builtin_mcp, "MCP_DISABLED", False)
    monkeypatch.setattr(builtin_mcp, "offline_mode", lambda: False)
    monkeypatch.setattr(
        builtin_mcp,
        "load_features",
        lambda: (_ for _ in ()).throw(RuntimeError("settings unavailable")),
    )

    def fail_create_task(coro):
        if inspect.iscoroutine(coro):
            coro.close()
        raise AssertionError("failed feature check should not create startup tasks")

    monkeypatch.setattr(builtin_mcp.asyncio, "create_task", fail_create_task)

    asyncio.run(builtin_mcp.register_builtin_servers(RejectingMcpManager()))
