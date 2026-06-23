"""Tests for Registry — tool registration, collision detection, adapter lifecycle."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_factory.manifest import Manifest, RuntimeSpec, ToolSpec
from mcp_factory.runtime.registry import Registry, CollisionError

_MOCK_SERVER = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def _make_manifest(name: str, tools: list[str] | None = None) -> Manifest:
    tools = tools or ["ping"]
    return Manifest(
        name=name,
        description=f"{name} bot",
        runtime=RuntimeSpec(type="python", command=sys.executable, script=str(_MOCK_SERVER)),
        tools=[ToolSpec(name=t, description=t.title()) for t in tools],
    )


class TestRegistration:
    def test_register_single_manifest(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("bot-a", ["tool1", "tool2"]))
        names = [e.qualified_name for e in reg.list_tools()]
        assert "bot-a.tool1" in names
        assert "bot-a.tool2" in names

    def test_qualified_name_format(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("fleet-health", ["fleet_status"]))
        entry = reg.get_tool("fleet-health.fleet_status")
        assert entry is not None
        assert entry.bot_name == "fleet-health"
        assert entry.tool_name == "fleet_status"

    def test_collision_raises(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("bot-a", ["ping"]))
        with pytest.raises(CollisionError, match="bot-a.ping"):
            reg.register_manifest(_make_manifest("bot-a", ["ping"]))

    def test_same_tool_name_different_bots_ok(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("bot-a", ["ping"]))
        reg.register_manifest(_make_manifest("bot-b", ["ping"]))
        assert reg.get_tool("bot-a.ping") is not None
        assert reg.get_tool("bot-b.ping") is not None

    def test_get_tool_missing_returns_none(self):
        reg = Registry()
        assert reg.get_tool("nonexistent.tool") is None

    def test_registered_bots(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("bot-a", ["t1"]))
        reg.register_manifest(_make_manifest("bot-b", ["t2"]))
        bots = reg.registered_bots()
        assert "bot-a" in bots
        assert "bot-b" in bots


class TestAdapterLifecycle:
    def test_call_tool_lazy_starts_adapter(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("mock-bot", ["ping"]))
        assert reg.adapter_status("mock-bot") == "idle"
        try:
            result = reg.call_tool("mock-bot.ping", {})
            assert result.get("content")
            assert reg.adapter_status("mock-bot") == "running"
        finally:
            reg.shutdown()

    def test_shutdown_stops_all_adapters(self):
        reg = Registry()
        reg.register_manifest(_make_manifest("mock-bot", ["ping"]))
        reg.call_tool("mock-bot.ping", {})
        reg.shutdown()
        assert reg.adapter_status("mock-bot") == "idle"

    def test_call_unknown_tool_raises(self):
        reg = Registry()
        with pytest.raises(KeyError, match="ghost.ping"):
            reg.call_tool("ghost.ping", {})


class TestIdleTimeout:
    def test_idle_bot_gets_reaped(self, monkeypatch):
        """Bot idle past the timeout window is stopped by reap_idle()."""
        import time
        monkeypatch.setenv("HUB_IDLE_TIMEOUT_MIN", "0.001")  # ~60 ms
        reg = Registry()
        reg.register_manifest(_make_manifest("mock-bot", ["ping"]))
        reg.call_tool("mock-bot.ping", {})
        assert reg.adapter_status("mock-bot") == "running"
        time.sleep(0.1)  # outlast the 60 ms window
        reaped = reg.reap_idle()
        assert "mock-bot" in reaped
        assert reg.adapter_status("mock-bot") != "running"

    def test_active_bot_not_reaped(self, monkeypatch):
        """Bot used moments ago is NOT stopped by reap_idle() within the window."""
        monkeypatch.setenv("HUB_IDLE_TIMEOUT_MIN", "15")
        reg = Registry()
        reg.register_manifest(_make_manifest("mock-bot", ["ping"]))
        reg.call_tool("mock-bot.ping", {})
        reaped = reg.reap_idle()
        assert reaped == []
        assert reg.adapter_status("mock-bot") == "running"
        reg.shutdown()
