"""Deliverable 6: --register flag tests.

--register = --scan --apply + --serve (composed shorthand).
Tests verify parsing and the scan+apply step without starting the hub.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from hub_server import _parse_args


_MINIMAL_YAML = """\
name: reg-test-bot
description: "Registration test"
runtime:
  type: python
  command: python
  output: /tmp/reg_server.py
tools:
  - name: ping
    description: "Ping"
    args: []
"""


class TestRegisterFlag:
    def test_register_flag_parsed(self):
        args = _parse_args(["--register"])
        assert args.register is True
        assert args.serve is False

    def test_register_mutually_exclusive_with_serve(self):
        with pytest.raises(SystemExit):
            _parse_args(["--register", "--serve"])

    def test_register_mutually_exclusive_with_scan(self):
        with pytest.raises(SystemExit):
            _parse_args(["--register", "--scan"])

    def test_register_accepts_scan_roots(self):
        args = _parse_args(["--register", "--scan-root", "/a", "--scan-root", "/b"])
        assert args.scan_roots == ["/a", "/b"]

    def test_register_applies_scan_then_calls_serve(self, tmp_path):
        """_run_register runs scan+apply, then calls _run_serve."""
        import hub_server as hs

        # Set up a temp claude.json target
        target = tmp_path / "claude.json"
        target.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        # Set up a real manifest to be discovered
        bot_dir = tmp_path / "reg-test-bot"
        bot_dir.mkdir()
        (bot_dir / "mcp.yaml").write_text(_MINIMAL_YAML)

        args = _parse_args([
            "--register",
            "--scan-root", str(tmp_path),
            "--scan-target", str(target),
        ])

        # Mock _run_serve to avoid actually starting the hub
        with patch.object(hs, "_run_serve", return_value=0) as mock_serve:
            result = hs._run_register(args)

        assert result == 0
        mock_serve.assert_called_once_with(args)

        # Verify the scan actually applied to target
        data = json.loads(target.read_text())
        assert "reg-test-bot" in data.get("mcpServers", {})

    def test_register_with_no_changes_still_calls_serve(self, tmp_path):
        """Even if scan finds nothing to add, serve still starts."""
        import hub_server as hs

        target = tmp_path / "claude.json"
        target.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        # Empty scan root — no manifests
        scan_root = tmp_path / "empty"
        scan_root.mkdir()

        args = _parse_args([
            "--register",
            "--scan-root", str(scan_root),
            "--scan-target", str(target),
        ])

        with patch.object(hs, "_run_serve", return_value=0) as mock_serve:
            result = hs._run_register(args)

        assert result == 0
        mock_serve.assert_called_once()
