"""Tests for scan mode — manifest discovery, diff computation, apply logic."""
from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_factory.scan import compute_scan, apply_scan, print_scan_report, scan_manifests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FLEET_HEALTH_YAML = Path(__file__).parent / "fixtures" / "fleet_health.yaml"
_MINIMAL_YAML = Path(__file__).parent / "fixtures" / "minimal.yaml"

# A valid mcp.yaml that CAN build a claude.json entry (has runtime.script pointing
# to an existing file — we use the mock server as a stand-in so the path resolves)
_MOCK_SERVER = Path(__file__).parent / "fixtures" / "mock_mcp_server.py"


def _write_manifest(directory: Path, name: str, script: Path | None = None) -> Path:
    """Write a minimal valid mcp.yaml into directory/<name>/mcp.yaml."""
    proj = directory / name
    proj.mkdir(parents=True, exist_ok=True)
    script_path = script or _MOCK_SERVER
    content = f"""name: {name}
description: Test bot {name}
runtime:
  type: python
  command: {sys.executable}
  script: {script_path}
tools:
  - name: ping
    description: Ping
env_required: []
env: {{}}
tags: []
priority: medium
"""
    yaml_path = proj / "mcp.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


def _write_invalid_manifest(directory: Path, name: str) -> Path:
    proj = directory / name
    proj.mkdir(parents=True, exist_ok=True)
    yaml_path = proj / "mcp.yaml"
    yaml_path.write_text("not: valid: yaml: [[\n", encoding="utf-8")
    return yaml_path


def _write_claude_json(path: Path, servers: dict) -> None:
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


# ---------------------------------------------------------------------------
# scan_manifests
# ---------------------------------------------------------------------------

class TestScanManifests:
    def test_finds_mcp_yaml_one_level_deep(self, tmp_path):
        _write_manifest(tmp_path, "bot-a")
        _write_manifest(tmp_path, "bot-b")
        results = scan_manifests(tmp_path)
        names = [m.name for _, m, _ in results if m is not None]
        assert "bot-a" in names
        assert "bot-b" in names

    def test_does_not_recurse_two_levels(self, tmp_path):
        nested = tmp_path / "bot-a" / "nested"
        nested.mkdir(parents=True)
        (nested / "mcp.yaml").write_text(
            "name: nested\ndescription: x\nruntime:\n  type: python\n  command: python\ntools: []\n",
            encoding="utf-8",
        )
        results = scan_manifests(tmp_path)
        # nested dir is two levels down — should NOT be found
        assert all(m is None or m.name != "nested" for _, m, _ in results)

    def test_load_error_captured(self, tmp_path):
        _write_invalid_manifest(tmp_path, "bad-bot")
        results = scan_manifests(tmp_path)
        errors = [(p, err) for p, _, err in results if err]
        assert len(errors) == 1

    def test_empty_root_returns_empty(self, tmp_path):
        assert scan_manifests(tmp_path) == []


# ---------------------------------------------------------------------------
# compute_scan
# ---------------------------------------------------------------------------

class TestComputeScan:
    def test_new_manifest_is_addition(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "new-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {})

        result = compute_scan(root, claude_json_path=claude)
        assert len(result.additions) == 1
        assert result.additions[0][0] == "new-bot"

    def test_existing_skipped_without_force(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "existing-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {"existing-bot": {"command": "python", "args": [], "env": {}}})

        result = compute_scan(root, claude_json_path=claude, force=False)
        assert len(result.additions) == 0
        assert any(name == "existing-bot" for name, _ in result.skipped)

    def test_existing_updated_with_force(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "my-bot")
        claude = tmp_path / ".claude.json"
        # Existing entry has different command
        _write_claude_json(claude, {"my-bot": {"command": "old-python", "args": [], "env": {}}})

        result = compute_scan(root, claude_json_path=claude, force=True)
        # Should be an update, not skipped
        update_names = [name for name, _, _ in result.updates]
        assert "my-bot" in update_names

    def test_load_error_captured_in_result(self, tmp_path):
        root = tmp_path / "projects"
        _write_invalid_manifest(root, "bad-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {})

        result = compute_scan(root, claude_json_path=claude)
        assert len(result.load_errors) == 1

    def test_no_claude_json_treats_as_empty(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "new-bot")
        nonexistent = tmp_path / "missing.json"

        result = compute_scan(root, claude_json_path=nonexistent)
        assert len(result.additions) == 1

    def test_multiple_manifests(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "bot-a")
        _write_manifest(root, "bot-b")
        _write_manifest(root, "bot-c")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {})

        result = compute_scan(root, claude_json_path=claude)
        assert len(result.additions) == 3


# ---------------------------------------------------------------------------
# apply_scan
# ---------------------------------------------------------------------------

class TestApplyScan:
    def test_apply_writes_additions_to_claude_json(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "new-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {})

        result = compute_scan(root, claude_json_path=claude)
        apply_scan(result, claude_json_path=claude)

        written = json.loads(claude.read_text())
        assert "new-bot" in written["mcpServers"]

    def test_apply_creates_backup(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "new-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {})

        result = compute_scan(root, claude_json_path=claude)
        backup = apply_scan(result, claude_json_path=claude)

        assert backup.exists()
        assert "scan-backup" in backup.name

    def test_apply_preserves_existing_entries(self, tmp_path):
        root = tmp_path / "projects"
        _write_manifest(root, "new-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {"preexisting": {"command": "x", "args": [], "env": {}}})

        result = compute_scan(root, claude_json_path=claude)
        apply_scan(result, claude_json_path=claude)

        written = json.loads(claude.read_text())
        assert "preexisting" in written["mcpServers"]
        assert "new-bot" in written["mcpServers"]

    def test_apply_does_not_touch_live_claude_json(self, tmp_path):
        """Ensure tests never accidentally modify ~/.claude.json."""
        live = Path.home() / ".claude.json"
        test_target = tmp_path / ".claude.json"
        _write_claude_json(test_target, {})
        root = tmp_path / "projects"
        _write_manifest(root, "safe-bot")

        result = compute_scan(root, claude_json_path=test_target)
        apply_scan(result, claude_json_path=test_target)

        # Only the test target was touched — live config unchanged
        assert test_target.exists()
        if live.exists():
            live_data = json.loads(live.read_text())
            assert "safe-bot" not in live_data.get("mcpServers", {})


# ---------------------------------------------------------------------------
# print_scan_report
# ---------------------------------------------------------------------------

class TestPrintScanReport:
    def test_dry_run_label_shown(self, tmp_path, capsys):
        root = tmp_path / "projects"
        _write_manifest(root, "bot-a")
        result = compute_scan(root, claude_json_path=tmp_path / "missing.json")
        print_scan_report(result, apply=False)
        out = capsys.readouterr().out
        assert "Dry run" in out

    def test_additions_listed(self, tmp_path, capsys):
        root = tmp_path / "projects"
        _write_manifest(root, "bot-a")
        result = compute_scan(root, claude_json_path=tmp_path / "missing.json")
        print_scan_report(result, apply=False)
        out = capsys.readouterr().out
        assert "bot-a" in out

    def test_skipped_listed(self, tmp_path, capsys):
        root = tmp_path / "projects"
        _write_manifest(root, "old-bot")
        claude = tmp_path / ".claude.json"
        _write_claude_json(claude, {"old-bot": {"command": "python", "args": [], "env": {}}})
        result = compute_scan(root, claude_json_path=claude)
        print_scan_report(result, apply=False)
        out = capsys.readouterr().out
        assert "old-bot" in out
        assert "already registered" in out
