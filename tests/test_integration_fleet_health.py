"""Integration test — hub spawns fleet-health subprocess and proxies tool calls.

Skipped automatically if the fleet-health server script does not exist on disk
(CI / environments where the server has not been set up yet).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_factory.manifest import load_manifest
from mcp_factory.runtime.subprocess_adapter import SubprocessAdapter

_FLEET_HEALTH_MANIFEST = Path(__file__).parent.parent / "examples" / "fleet_health.yaml"
_fleet_health_server_env = os.environ.get("FLEET_HEALTH_SERVER_PATH", "")
_FLEET_HEALTH_SERVER: Path | None = Path(_fleet_health_server_env) if _fleet_health_server_env else None


@pytest.fixture(scope="module")
def fleet_manifest():
    return load_manifest(_FLEET_HEALTH_MANIFEST)


def _fleet_server_available() -> bool:
    return _FLEET_HEALTH_SERVER is not None and _FLEET_HEALTH_SERVER.exists()


@pytest.mark.skipif(
    not _fleet_server_available(),
    reason="fleet-health server not available (set FLEET_HEALTH_SERVER_PATH env var to the server.py path)",
)
class TestFleetHealthIntegration:
    def test_hub_spawns_fleet_health_subprocess(self, fleet_manifest):
        adapter = SubprocessAdapter(fleet_manifest)
        try:
            assert not adapter.is_alive
            adapter.call_tool("fleet_status", {})
            assert adapter.is_alive
        finally:
            adapter.stop()

    def test_fleet_status_returns_json(self, fleet_manifest):
        adapter = SubprocessAdapter(fleet_manifest)
        try:
            result = adapter.call_tool("fleet_status", {})
            content = result.get("content", [])
            assert len(content) >= 1
            text = content[0]["text"]
            # Should be parseable JSON or a status report
            assert isinstance(text, str)
            assert len(text) > 0
        finally:
            adapter.stop()

    def test_bot_status_tool_works(self, fleet_manifest):
        adapter = SubprocessAdapter(fleet_manifest)
        try:
            result = adapter.call_tool("bot_status", {"bot_name": "kronos"})
            content = result.get("content", [])
            assert len(content) >= 1
        finally:
            adapter.stop()

    def test_tool_names_match_manifest(self, fleet_manifest):
        tool_names = fleet_manifest.tool_names
        assert "fleet_status" in tool_names
        assert "bot_status" in tool_names
        assert "recent_alerts" in tool_names
        assert "dump_markdown_report" in tool_names

    def test_stop_cleans_up_subprocess(self, fleet_manifest):
        adapter = SubprocessAdapter(fleet_manifest)
        adapter.call_tool("fleet_status", {})
        proc = adapter._proc
        adapter.stop()
        # Process should have exited cleanly
        assert proc is not None
        assert proc.poll() is not None  # not None = has exited
