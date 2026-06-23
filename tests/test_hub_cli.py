"""Tests for hub_server.py --scan CLI flag (no --apply, no live config touched)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import hub_server

_MOCK_SERVER = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def _write_manifest(directory: Path, name: str) -> Path:
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
priority: medium
"""
    yaml_path = proj / "mcp.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


class TestScanCLI:
    def test_scan_dry_run_exit_zero(self, tmp_path, capsys):
        root = tmp_path / "projects"
        _write_manifest(root, "bot-a")
        rc = hub_server.run(["--scan", str(root)])
        assert rc == 0

    def test_scan_dry_run_shows_additions(self, tmp_path, capsys):
        root = tmp_path / "projects"
        _write_manifest(root, "bot-a")
        hub_server.run(["--scan", str(root)])
        out = capsys.readouterr().out
        assert "bot-a" in out

    def test_scan_dry_run_no_files_written(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "bot-a")
        hub_server.run(["--scan", str(root)])
        # No .claude.json should have been created
        assert not (Path.home() / ".claude.json.scan-backup-test").exists()

    def test_scan_apply_writes_config(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "my-bot")
        target = tmp_path / ".claude.json"
        target.write_text('{"mcpServers": {}}', encoding="utf-8")

        rc = hub_server.run(["--scan", str(root), "--apply", "--scan-target", str(target)])
        assert rc == 0
        written = json.loads(target.read_text())
        assert "my-bot" in written["mcpServers"]

    def test_scan_apply_creates_backup(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "my-bot")
        target = tmp_path / ".claude.json"
        target.write_text('{"mcpServers": {}}', encoding="utf-8")

        hub_server.run(["--scan", str(root), "--apply", "--scan-target", str(target)])

        # Backup is created next to the target
        backups = list(tmp_path.glob(".claude.json.scan-backup-*"))
        assert len(backups) == 1

    def test_scan_skip_without_force(self, tmp_path, capsys):
        root = tmp_path / "projects"
        _write_manifest(root, "existing-bot")
        target = tmp_path / ".claude.json"
        target.write_text(
            json.dumps({"mcpServers": {"existing-bot": {"command": "old", "args": [], "env": {}}}}),
            encoding="utf-8",
        )

        hub_server.run(["--scan", str(root), "--scan-target", str(target)])
        out = capsys.readouterr().out
        assert "already registered" in out

    def test_factory_mode_still_works(self, tmp_path):
        manifest_path = Path(__file__).parent / "fixtures" / "fleet_health.yaml"
        output_config = tmp_path / "out.json"
        rc = hub_server.run([
            "--manifest", str(manifest_path),
            "--output-config", str(output_config),
            "--dry-run",
        ])
        assert rc == 0
