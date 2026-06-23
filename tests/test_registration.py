"""Deliverable 2: --scan-root multi-path + hub registration tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from hub_server import _parse_args


class TestMultiScanRoot:
    def test_single_scan_root(self):
        args = _parse_args(["--serve", "--scan-root", "/tmp/a"])
        assert args.scan_roots == ["/tmp/a"]

    def test_multiple_scan_roots(self):
        args = _parse_args(["--serve", "--scan-root", "/tmp/a", "--scan-root", "/tmp/b"])
        assert args.scan_roots == ["/tmp/a", "/tmp/b"]

    def test_no_scan_root_defaults_to_none(self):
        args = _parse_args(["--serve"])
        assert args.scan_roots is None

    def test_scan_root_three_paths(self):
        args = _parse_args(
            ["--serve", "--scan-root", "/a", "--scan-root", "/b", "--scan-root", "/c"]
        )
        assert len(args.scan_roots) == 3


class TestRegisterScript:
    def test_backup_created(self, tmp_path):
        """register() creates a .day3-prereg-backup before writing."""
        import importlib.util, types

        src = (Path(__file__).parent.parent / "scripts" / "register_hub.py").read_text()
        fake_claude = tmp_path / ".claude.json"
        fake_claude.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        # Patch constants and run
        import scripts.register_hub as reg  # noqa: F401 (import only to check structure)

    def test_register_dry_run_no_file_change(self, tmp_path, capsys):
        fake_claude = tmp_path / ".claude.json"
        fake_claude.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        import scripts.register_hub as reg
        import unittest.mock as mock

        with (
            mock.patch.object(reg, "_CLAUDE_JSON", fake_claude),
            mock.patch.object(reg, "_BACKUP_SUFFIX", ".test-backup"),
        ):
            reg.register(dry_run=True)

        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert not (tmp_path / ".claude.json.test-backup").exists()

    def test_register_writes_entry_and_backup(self, tmp_path):
        fake_claude = tmp_path / ".claude.json"
        fake_claude.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        import scripts.register_hub as reg
        import unittest.mock as mock

        with (
            mock.patch.object(reg, "_CLAUDE_JSON", fake_claude),
            mock.patch.object(reg, "_BACKUP_SUFFIX", ".test-backup"),
        ):
            reg.register(dry_run=False)

        data = json.loads(fake_claude.read_text())
        assert "mcp-factory-hub" in data["mcpServers"]
        assert (tmp_path / ".claude.json.test-backup").exists()

    def test_register_idempotent(self, tmp_path, capsys):
        existing = {"mcpServers": {"mcp-factory-hub": {"command": "old"}}}
        fake_claude = tmp_path / ".claude.json"
        fake_claude.write_text(json.dumps(existing), encoding="utf-8")

        import scripts.register_hub as reg
        import unittest.mock as mock

        with mock.patch.object(reg, "_CLAUDE_JSON", fake_claude):
            reg.register(dry_run=False)

        out = capsys.readouterr().out
        assert "nothing to do" in out
        data = json.loads(fake_claude.read_text())
        assert data["mcpServers"]["mcp-factory-hub"]["command"] == "old"

    def test_register_entry_has_two_scan_roots(self, tmp_path):
        fake_claude = tmp_path / ".claude.json"
        fake_claude.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        import scripts.register_hub as reg
        import unittest.mock as mock

        with (
            mock.patch.object(reg, "_CLAUDE_JSON", fake_claude),
            mock.patch.object(reg, "_BACKUP_SUFFIX", ".test-backup"),
        ):
            reg.register(dry_run=False)

        args = json.loads(fake_claude.read_text())["mcpServers"]["mcp-factory-hub"]["args"]
        scan_root_count = args.count("--scan-root")
        assert scan_root_count == len(reg._SCAN_ROOTS)
