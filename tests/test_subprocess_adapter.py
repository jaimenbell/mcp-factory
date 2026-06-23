"""Tests for SubprocessAdapter — spawns the mock_mcp_server.py fixture."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow running tests from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_factory.manifest import Manifest, RuntimeSpec, ToolSpec
from mcp_factory.runtime.subprocess_adapter import SubprocessAdapter, SubprocessError

_MOCK_SERVER = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def _make_manifest(name: str = "mock-bot") -> Manifest:
    """Build a manifest pointing at the mock server using the current Python."""
    return Manifest(
        name=name,
        description="Mock bot for testing",
        runtime=RuntimeSpec(
            type="python",
            command=sys.executable,
            script=str(_MOCK_SERVER),
        ),
        tools=[
            ToolSpec(name="ping", description="Ping"),
            ToolSpec(name="echo", description="Echo"),
            ToolSpec(name="crash", description="Crash"),
        ],
    )


class TestSubprocessAdapterHappyPath:
    def test_ping_returns_pong(self):
        adapter = SubprocessAdapter(_make_manifest())
        try:
            result = adapter.call_tool("ping", {})
            content = result.get("content", [])
            assert len(content) == 1
            assert content[0]["type"] == "text"
            assert '"pong": true' in content[0]["text"]
        finally:
            adapter.stop()

    def test_echo_returns_message(self):
        adapter = SubprocessAdapter(_make_manifest())
        try:
            result = adapter.call_tool("echo", {"message": "hello"})
            text = result["content"][0]["text"]
            assert "hello" in text
        finally:
            adapter.stop()

    def test_adapter_is_alive_after_call(self):
        adapter = SubprocessAdapter(_make_manifest())
        try:
            adapter.call_tool("ping", {})
            assert adapter.is_alive
        finally:
            adapter.stop()

    def test_adapter_not_alive_before_call(self):
        adapter = SubprocessAdapter(_make_manifest())
        assert not adapter.is_alive
        adapter.stop()  # no-op when not started

    def test_multiple_calls_reuse_subprocess(self):
        adapter = SubprocessAdapter(_make_manifest())
        try:
            r1 = adapter.call_tool("ping", {})
            r2 = adapter.call_tool("ping", {})
            assert r1 == r2
        finally:
            adapter.stop()

    def test_stop_makes_adapter_not_alive(self):
        adapter = SubprocessAdapter(_make_manifest())
        adapter.call_tool("ping", {})
        adapter.stop()
        assert not adapter.is_alive

    def test_stop_is_idempotent(self):
        adapter = SubprocessAdapter(_make_manifest())
        adapter.call_tool("ping", {})
        adapter.stop()
        adapter.stop()  # should not raise


class TestSubprocessAdapterErrors:
    def test_tool_error_raises_subprocess_error(self):
        adapter = SubprocessAdapter(_make_manifest())
        try:
            with pytest.raises(SubprocessError, match="crash"):
                adapter.call_tool("crash", {})
        finally:
            adapter.stop()

    def test_bad_script_raises_on_first_call(self):
        manifest = Manifest(
            name="bad-bot",
            description="Bad script",
            runtime=RuntimeSpec(
                type="python",
                command=sys.executable,
                script=str(_MOCK_SERVER.parent / "nonexistent_server.py"),
            ),
            tools=[ToolSpec(name="ping", description="Ping")],
        )
        # has_existing_script will be False so adapter falls through to output_path=None
        adapter = SubprocessAdapter(manifest)
        try:
            with pytest.raises(SubprocessError):
                adapter.call_tool("ping", {})
        finally:
            adapter.stop()
