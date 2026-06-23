"""Tests for generator (scaffold) and config writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_factory.manifest import load_manifest
from mcp_factory.generator import generate_server, scaffold_summary
from mcp_factory.config import build_claude_entry, compare_entries, write_config_file

FIXTURES = Path(__file__).parent / "fixtures"

# Expected claude.json entry for the fleet-health fixture. The script path is
# the fixture's own stub, resolved the same way load_manifest resolves a
# relative runtime.script — so this matches on any machine.
LIVE_FLEET_HEALTH_ENTRY = {
    "command": "C:\\Python314\\python.exe",
    "args": [str((FIXTURES / "fleet_health_server.py").resolve())],
    "env": {},
}


class TestScaffoldSummary:
    def test_existing_script_returns_reference(self):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        summary = scaffold_summary(m, Path("/tmp/generated"))
        assert summary["action"] == "reference_existing"
        assert "server.py" in summary["script"]
        assert set(summary["tools"]) == {
            "fleet_status", "bot_status", "recent_alerts", "dump_markdown_report"
        }

    def test_new_script_returns_scaffold(self):
        m = load_manifest(FIXTURES / "minimal.yaml")
        summary = scaffold_summary(m, Path("/tmp/generated"))
        assert summary["action"] == "scaffold_new"
        assert "test_bot_server.py" in summary["output_path"]
        assert summary["tools"] == ["ping"]


class TestGenerateServer:
    def test_generate_minimal_creates_file(self, tmp_path):
        m = load_manifest(FIXTURES / "minimal.yaml")
        output = generate_server(m, tmp_path)
        assert output is not None
        assert output.exists()

    def test_generated_file_contains_tool_names(self, tmp_path):
        m = load_manifest(FIXTURES / "minimal.yaml")
        output = generate_server(m, tmp_path)
        content = output.read_text()
        assert 'name="ping"' in content
        assert 'Server("test-bot")' in content

    def test_existing_script_skips_generation(self, tmp_path):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        result = generate_server(m, tmp_path)
        # Should return None — existing script, no scaffold needed
        assert result is None
        # No files should have been created in tmp_path
        assert list(tmp_path.iterdir()) == []

    def test_no_overwrite_without_force(self, tmp_path):
        m = load_manifest(FIXTURES / "minimal.yaml")
        generate_server(m, tmp_path)
        with pytest.raises(FileExistsError):
            generate_server(m, tmp_path, force=False)

    def test_force_overwrites(self, tmp_path):
        m = load_manifest(FIXTURES / "minimal.yaml")
        generate_server(m, tmp_path)
        result = generate_server(m, tmp_path, force=True)
        assert result is not None and result.exists()

    def test_dry_run_no_file_written(self, tmp_path):
        m = load_manifest(FIXTURES / "minimal.yaml")
        result = generate_server(m, tmp_path, dry_run=True)
        assert result is None
        assert list(tmp_path.iterdir()) == []

    def test_generated_file_has_all_tools(self, tmp_path):
        """Fleet-health has 4 tools — verify all appear in a generated stub."""
        # Create a variant manifest that has no existing script so scaffold fires
        import textwrap
        manifest_text = textwrap.dedent("""\
            name: fleet-health-stub
            description: Test scaffold for fleet-health tool surface.
            runtime:
              type: python
              command: "C:\\\\Python314\\\\python.exe"
            tools:
              - name: fleet_status
                description: Get fleet status.
                args: []
              - name: bot_status
                description: Get single bot status.
                args:
                  - name: bot_name
                    type: string
                    required: true
                    description: Bot name.
              - name: recent_alerts
                description: List alerts.
                args:
                  - name: hours
                    type: number
                    required: false
                    description: Hours.
              - name: dump_markdown_report
                description: Full markdown report.
                args: []
            priority: high
        """)
        mf = tmp_path / "fh_stub.yaml"
        mf.write_text(manifest_text)
        m = load_manifest(mf)
        out = generate_server(m, tmp_path)
        content = out.read_text()
        for tool in ["fleet_status", "bot_status", "recent_alerts", "dump_markdown_report"]:
            assert f'name="{tool}"' in content, f"Tool '{tool}' not found in generated stub"


class TestBuildClaudeEntry:
    def test_fleet_health_entry_matches_live(self):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        entry = build_claude_entry(m)
        matches, diffs = compare_entries(entry, LIVE_FLEET_HEALTH_ENTRY)
        assert matches, f"Entry mismatch: {diffs}"

    def test_entry_structure(self):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        entry = build_claude_entry(m)
        assert "command" in entry
        assert "args" in entry
        assert "env" in entry
        assert isinstance(entry["args"], list)
        assert isinstance(entry["env"], dict)

    def test_entry_no_script_no_generated_raises(self):
        """Manifest with no script AND no generated output → error."""
        import textwrap
        from mcp_factory.manifest import Manifest, load_manifest
        m_text = textwrap.dedent("""\
            name: no-script-bot
            description: No script specified.
            runtime:
              type: python
              command: python.exe
            tools:
              - name: ping
                description: pong
                args: []
        """)
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(m_text)
            fname = f.name
        try:
            m = load_manifest(fname)
            with pytest.raises(ValueError, match="no existing script"):
                build_claude_entry(m)
        finally:
            os.unlink(fname)

    def test_env_passthrough(self, tmp_path):
        import textwrap
        m_text = textwrap.dedent("""\
            name: env-bot
            description: Bot with env vars.
            runtime:
              type: python
              command: python.exe
              script: __file__
            tools:
              - name: ping
                description: pong
                args: []
            env:
              MY_KEY: my_value
              OTHER: 123
        """)
        # Patch script to this test file so has_existing_script returns True
        m_text = m_text.replace("__file__", str(Path(__file__).resolve()).replace("\\", "\\\\"))
        mf = tmp_path / "env_bot.yaml"
        mf.write_text(m_text)
        m = load_manifest(mf)
        entry = build_claude_entry(m)
        assert entry["env"] == {"MY_KEY": "my_value", "OTHER": "123"}


class TestWriteConfigFile:
    def test_writes_json_file(self, tmp_path):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        out = tmp_path / "test_config.json"
        write_config_file([m], out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert "mcpServers" in data
        assert "fleet-health" in data["mcpServers"]

    def test_dry_run_no_file(self, tmp_path):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        out = tmp_path / "should_not_exist.json"
        write_config_file([m], out, dry_run=True)
        assert not out.exists()

    def test_multiple_manifests(self, tmp_path):
        m1 = load_manifest(FIXTURES / "fleet_health.yaml")
        # Use minimal as generated (supply a generated_script so it doesn't raise)
        m2 = load_manifest(FIXTURES / "minimal.yaml")
        fake_script = tmp_path / "test_bot_server.py"
        fake_script.write_text("# stub")
        out = tmp_path / "multi.json"
        write_config_file([m1, m2], out, generated_scripts={"test-bot": fake_script})
        data = json.loads(out.read_text())
        assert "fleet-health" in data["mcpServers"]
        assert "test-bot" in data["mcpServers"]


class TestCompareEntries:
    def test_identical_entries_match(self):
        entry = {
            "command": "C:\\Python314\\python.exe",
            "args": ["C:\\path\\to\\server.py"],
            "env": {},
        }
        matches, diffs = compare_entries(entry, entry)
        assert matches
        assert diffs == []

    def test_different_command_detected(self):
        a = {"command": "C:\\Python314\\python.exe", "args": [], "env": {}}
        b = {"command": "C:\\Python312\\python.exe", "args": [], "env": {}}
        matches, diffs = compare_entries(a, b)
        assert not matches
        assert any("command" in d for d in diffs)

    def test_env_diff_detected(self):
        a = {"command": "py", "args": [], "env": {"A": "1"}}
        b = {"command": "py", "args": [], "env": {}}
        matches, diffs = compare_entries(a, b)
        assert not matches
        assert any("env" in d for d in diffs)
