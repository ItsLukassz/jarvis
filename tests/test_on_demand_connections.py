"""A connection declared `"onDemand": true` costs no context until it is
switched on, and goes back off when the brain next restarts."""
import asyncio
import json

import pytest


@pytest.fixture
def server(monkeypatch):
    import server as srv
    report = lambda: srv.ConnectionsReport(servers={
        "chrome-devtools": {"command": "npx", "args": ["x"], "onDemand": True},
        "notion": {"command": "npx", "args": ["y"]},
    })
    monkeypatch.setattr(srv, "declared_connections", report)
    monkeypatch.setattr(srv, "_on_demand_enabled", set())
    monkeypatch.setattr(srv, "_staged_enable", [])
    return srv


def _servers(srv, tmp_path) -> dict:
    return json.loads(srv._write_mcp_config(tmp_path).read_text(encoding="utf-8"))["mcpServers"]


def test_an_on_demand_server_is_left_out_until_it_is_enabled(server, tmp_path):
    servers = _servers(server, tmp_path)
    assert "notion" in servers and "chrome-devtools" not in servers
    assert server.LAST_CONNECTIONS.on_demand == ["chrome-devtools"]

    server._on_demand_enabled.add("chrome-devtools")
    servers = _servers(server, tmp_path)
    assert "onDemand" not in servers["chrome-devtools"], "JARVIS's own key never reaches the CLI"
    assert server.LAST_CONNECTIONS.on_demand == []


def test_enable_connection_stages_only_what_is_on_demand(server, tmp_path):
    _servers(server, tmp_path)
    run = lambda name: asyncio.run(server.tool_enable_connection({"name": name}))
    assert "already on" in run("notion")
    assert "no on-demand connection" in run("made-up")
    assert server._staged_enable == []
    assert "One moment" in run("chrome-devtools")
    assert server._staged_enable == ["chrome-devtools"]


def test_they_go_back_off_when_the_brain_restarts_anyway(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server.jarvis_memory, "ensure_layout", lambda: tmp_path)
    monkeypatch.setattr(server, "brain_instance", None)
    server._on_demand_enabled.add("chrome-devtools")
    server._retire_on_demand()
    assert server._on_demand_enabled == set()
    assert server.LAST_CONNECTIONS.on_demand == ["chrome-devtools"]
