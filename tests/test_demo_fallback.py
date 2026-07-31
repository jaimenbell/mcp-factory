"""Tests for the bundled demo-bot fallback (--serve with zero real manifests).

Directory-listing honesty: mcp-factory scores 1 tool (the always-present
_hub.list_bots) when its scan root is empty (e.g. Glama's sandbox, where
MCP_FACTORY_SCAN_ROOT defaults to an empty ~/projects). The fallback loads a
bundled, unmistakably-labelled demo manifest ONLY in that exact situation --
never overriding an operator's explicit, intentional empty scan root, and
never when opted out.

_run_serve is exercised directly (not via subprocess) with
mcp_factory.runtime.hub.run_hub mocked out, so no real stdio MCP server
starts; we just inspect the manifest list it would have been called with.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import mcp_factory.cli as cli_mod
from mcp_factory.cli import _parse_args, _run_serve
from mcp_factory.demo import DEMO_BOT_NAME

_MOCK_SERVER = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def _write_real_manifest(directory: Path, name: str) -> Path:
    proj = directory / name
    proj.mkdir(parents=True, exist_ok=True)
    content = f"""name: {name}
description: Test bot {name}
runtime:
  type: python
  command: {sys.executable}
  script: {_MOCK_SERVER}
tools:
  - name: ping
    description: Ping
"""
    yaml_path = proj / "mcp.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


def _run_serve_and_capture(args) -> list:
    """Run _run_serve with run_hub mocked; return the manifests list it received."""
    with patch.object(cli_mod, "asyncio") as mock_asyncio:
        with patch("mcp_factory.runtime.hub.run_hub", new_callable=AsyncMock) as mock_run_hub:
            # asyncio.run(coro) just needs to not actually run an event loop,
            # but the coroutine object it's handed must still be closed or
            # Python warns "coroutine was never awaited" at GC time.
            mock_asyncio.run.side_effect = lambda coro: coro.close()
            rc = _run_serve(args)
    assert rc == 0
    assert mock_run_hub.await_count == 1 or mock_run_hub.call_count == 1
    (manifests,), _ = mock_run_hub.call_args
    return manifests


class TestDemoFallback:
    def test_empty_root_no_override_loads_demo(self, tmp_path, capsys, monkeypatch):
        """(a) empty/nonexistent scan root, no explicit override -> demo loads,
        exactly one demo bot registers, and the stderr marker fires."""
        monkeypatch.setattr(cli_mod, "_DEFAULT_SCAN_ROOT", tmp_path / "does-not-exist")
        monkeypatch.delenv("MCP_FACTORY_SCAN_ROOT", raising=False)
        args = _parse_args(["--serve"])

        manifests = _run_serve_and_capture(args)

        assert len(manifests) == 1
        assert manifests[0].name == DEMO_BOT_NAME

        err = capsys.readouterr().err
        assert DEMO_BOT_NAME in err
        assert "DEMO" in err
        assert "NOT a real integration" in err

    def test_real_manifest_present_demo_absent(self, tmp_path, capsys, monkeypatch):
        """(b) scan root containing a real */mcp.yaml -> demo is ABSENT, only
        real manifests register."""
        monkeypatch.delenv("MCP_FACTORY_SCAN_ROOT", raising=False)
        root = tmp_path / "projects"
        _write_real_manifest(root, "real-bot")
        args = _parse_args(["--serve", "--scan-root", str(root)])

        manifests = _run_serve_and_capture(args)

        names = [m.name for m in manifests]
        assert names == ["real-bot"]
        assert DEMO_BOT_NAME not in names

        err = capsys.readouterr().err
        assert DEMO_BOT_NAME not in err

    def test_explicit_empty_scan_root_respected_no_demo(self, tmp_path, capsys, monkeypatch):
        """(c) explicit --scan-root pointing at an empty dir -> demo does NOT
        fire; an intentional empty result is respected as-is."""
        monkeypatch.delenv("MCP_FACTORY_SCAN_ROOT", raising=False)
        empty_root = tmp_path / "empty-projects"
        empty_root.mkdir()
        args = _parse_args(["--serve", "--scan-root", str(empty_root)])

        manifests = _run_serve_and_capture(args)

        assert manifests == []
        err = capsys.readouterr().err
        assert DEMO_BOT_NAME not in err

    def test_explicit_empty_scan_root_env_var_respected_no_demo(self, tmp_path, capsys, monkeypatch):
        """(c, env-var variant) explicit MCP_FACTORY_SCAN_ROOT pointing at an
        empty dir -> demo does NOT fire, with NO --scan-root flag passed (the
        override is detected purely from the env var)."""
        empty_root = tmp_path / "empty-projects-env"
        empty_root.mkdir()
        # _DEFAULT_SCAN_ROOT is cached at import time from the env var, so a
        # monkeypatched setenv() alone wouldn't change what root is actually
        # scanned here -- patch it to mirror what a real process launched with
        # this env var set would have resolved at import time. The point under
        # test is _run_serve's *override detection*, which re-reads
        # os.environ at call time independent of this caching.
        monkeypatch.setattr(cli_mod, "_DEFAULT_SCAN_ROOT", empty_root)
        monkeypatch.setenv("MCP_FACTORY_SCAN_ROOT", str(empty_root))
        args = _parse_args(["--serve"])
        assert args.scan_roots is None  # no --scan-root flag passed

        manifests = _run_serve_and_capture(args)

        assert manifests == []
        err = capsys.readouterr().err
        assert DEMO_BOT_NAME not in err

    def test_no_demo_flag_disables_fallback(self, tmp_path, capsys, monkeypatch):
        """(d) --no-demo set -> demo does NOT fire even on an empty default root."""
        monkeypatch.setattr(cli_mod, "_DEFAULT_SCAN_ROOT", tmp_path / "does-not-exist")
        monkeypatch.delenv("MCP_FACTORY_SCAN_ROOT", raising=False)
        args = _parse_args(["--serve", "--no-demo"])

        manifests = _run_serve_and_capture(args)

        assert manifests == []
        err = capsys.readouterr().err
        assert DEMO_BOT_NAME not in err
        assert "no-demo" in err or "NO_DEMO" in err

    def test_no_demo_env_var_disables_fallback(self, tmp_path, capsys, monkeypatch):
        """(d, env-var variant) MCP_FACTORY_NO_DEMO=1 -> demo does NOT fire."""
        monkeypatch.setattr(cli_mod, "_DEFAULT_SCAN_ROOT", tmp_path / "does-not-exist")
        monkeypatch.delenv("MCP_FACTORY_SCAN_ROOT", raising=False)
        monkeypatch.setenv("MCP_FACTORY_NO_DEMO", "1")
        args = _parse_args(["--serve"])

        manifests = _run_serve_and_capture(args)

        assert manifests == []
        err = capsys.readouterr().err
        assert DEMO_BOT_NAME not in err

    def test_demo_manifest_loads_standalone(self):
        """load_demo_manifest() itself returns a valid, unmistakably-labelled
        manifest independent of _run_serve's fallback logic."""
        from mcp_factory.demo import load_demo_manifest

        manifest = load_demo_manifest()
        assert manifest is not None
        assert manifest.name == DEMO_BOT_NAME
        assert "DEMO" in manifest.description
        assert "not a real integration" in manifest.description.lower()
        assert manifest.tools, "demo manifest should expose at least one tool"
        for tool in manifest.tools:
            assert "[DEMO]" in tool.description
        # runtime.command gets pinned to the live interpreter, not the yaml's
        # literal placeholder value.
        assert manifest.runtime.command == sys.executable
