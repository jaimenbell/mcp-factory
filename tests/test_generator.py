"""Tests for generator (scaffold) and config writer."""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from mcp_factory.manifest import load_manifest, Manifest, RuntimeSpec, ToolSpec
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


class TestSecurityCodegenInjection:
    """P1: a malicious name must never reach codegen; parse fails closed so no
    server file is produced. Valid manifests are unchanged (no regression)."""

    MALICIOUS = 'x";import os;os.system("calc")#'

    def _malicious_manifest_yaml(self, out_target: str) -> str:
        return textwrap.dedent(f"""\
            name: '{self.MALICIOUS}'
            description: pwned
            runtime:
              type: python
              command: python.exe
              output: {out_target}
            tools:
              - name: ping
                description: p
        """)

    def test_malicious_manifest_never_generates_file(self, tmp_path):
        target = tmp_path / "evil_server.py"
        mf = tmp_path / "evil.yaml"
        mf.write_text(self._malicious_manifest_yaml(str(target.name)))
        # Parse must fail closed — before any codegen.
        with pytest.raises(ValueError):
            m = load_manifest(mf)
            generate_server(m, tmp_path)
        assert not target.exists()

    def test_valid_manifest_output_unchanged(self, tmp_path):
        """Regression: a clean manifest generates the same content as before."""
        m = load_manifest(FIXTURES / "minimal.yaml")
        out = generate_server(m, tmp_path)
        content = out.read_text()
        assert 'name="ping"' in content
        assert 'Server("test-bot")' in content
        # No injected import/os.system leaked in.
        assert "os.system" not in content


class TestSecurityCodegenInjectionResidual:
    """P0/P1: free-text manifest fields (description, runtime.command,
    tool/arg.description) and env_required are the residual injection surface
    beyond the name fields. Each crafted-but-BENIGN payload (MARKER only) must
    result in EITHER a parse rejection OR generated output that compiles/parses
    with the payload inert inside a proper string/comment (never live code).

    Generated servers are ONLY compile/parse-checked here — never executed."""

    def _write_manifest(self, tmp_path, body: str) -> Manifest:
        mf = tmp_path / "m.yaml"
        mf.write_text(body, encoding="utf-8")
        return load_manifest(mf)

    # --- env_required (worst vector): rejected at parse, no file ever written ---
    def test_env_required_injection_never_generates(self, tmp_path):
        payload = 'A"]; import os; os.system("MARKER_ENV")  #'
        body = textwrap.dedent(f"""\
            name: evil
            description: d
            runtime:
              type: python
              command: python.exe
              style: fastmcp
            env_required:
              - '{payload}'
            tools:
              - name: ping
                description: p
        """)
        with pytest.raises(ValueError, match="env_required"):
            m = self._write_manifest(tmp_path, body)
            generate_server(m, tmp_path)
        # No .py scaffold produced.
        assert not list(tmp_path.glob("*_server.py"))

    # --- node runtime.command: newline must not end the // comment ---
    @pytest.mark.skipif(not shutil.which("node"), reason="node not available")
    def test_node_command_injection_parses_and_is_inert(self, tmp_path):
        # A newline in the command previously dropped the tail into live JS.
        body = (
            "name: nodebot\n"
            "description: legit\n"
            "runtime:\n"
            "  type: node\n"
            '  command: "node\\nrequire(\\"child_process\\").execSync(\\"MARKER_CMD\\");//"\n'
            "tools:\n"
            "  - name: ping\n"
            "    description: p\n"
        )
        m = self._write_manifest(tmp_path, body)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        proc = subprocess.run(
            ["node", "--check", str(out)], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"generated JS failed to parse: {proc.stderr}"
        # The payload must live inside a // comment line, never as bare JS.
        for line in content.splitlines():
            if "MARKER_CMD" in line:
                assert line.lstrip().startswith("//"), f"live injected JS: {line!r}"

    # --- node description: ALL line terminators (\r, U+2028, U+2029) neutralized ---
    @pytest.mark.skipif(not shutil.which("node"), reason="node not available")
    @pytest.mark.parametrize("term", ["\\r", "\\u2028", "\\u2029"])
    def test_node_description_terminator_injection_parses(self, tmp_path, term):
        body = (
            "name: nodebot\n"
            f'description: "legit{term}require(\\"child_process\\").execSync(\\"MARKER_DESC\\");//"\n'
            "runtime:\n"
            "  type: node\n"
            "  command: node\n"
            "tools:\n"
            "  - name: ping\n"
            "    description: p\n"
        )
        m = self._write_manifest(tmp_path, body)
        out = generate_server(m, tmp_path)
        proc = subprocess.run(
            ["node", "--check", str(out)], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"generated JS failed to parse: {proc.stderr}"

    # --- node tool.description: rendered via json.dumps -> valid inert literal ---
    @pytest.mark.skipif(not shutil.which("node"), reason="node not available")
    def test_node_tool_description_injection_parses(self, tmp_path):
        body = (
            "name: nodebot\n"
            "description: d\n"
            "runtime:\n  type: node\n  command: node\n"
            "tools:\n"
            "  - name: ping\n"
            '    description: "x\\nrequire(\\"child_process\\").execSync(\\"MARKER_TOOL\\")"\n'
        )
        m = self._write_manifest(tmp_path, body)
        out = generate_server(m, tmp_path)
        proc = subprocess.run(
            ["node", "--check", str(out)], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"generated JS failed to parse: {proc.stderr}"

    # --- python tool.description / arg.description: repr -> inert string literal ---
    def test_python_tool_and_arg_description_injection_compiles(self, tmp_path):
        body = (
            "name: pybot\n"
            "description: d\n"
            "runtime:\n  type: python\n  command: python.exe\n  style: fastmcp\n"
            "tools:\n"
            "  - name: ping\n"
            "    description: 'x\"; import os; os.system(\"MARKER_TD\")  #'\n"
            "    args:\n"
            "      - name: a\n"
            "        type: string\n"
            "        description: 'y\"; import os; os.system(\"MARKER_AD\")  #'\n"
        )
        m = self._write_manifest(tmp_path, body)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        # Must compile (no breakout SyntaxError) ...
        compile(content, str(out), "exec")
        # ... and the payload must sit inside a quoted description=... literal.
        for marker in ("MARKER_TD", "MARKER_AD"):
            for line in content.splitlines():
                if marker in line:
                    assert "description=" in line, f"payload escaped literal: {line!r}"

    # --- python docstring description: cannot close the r\"\"\" docstring ---
    def test_python_docstring_triple_quote_injection_compiles(self, tmp_path):
        body = (
            "name: pybot\n"
            "description: 'break \"\"\" import os; os.system(\"MARKER_DOC\")'\n"
            "runtime:\n  type: python\n  command: python.exe\n  style: fastmcp\n"
            "tools:\n  - name: ping\n    description: p\n"
        )
        m = self._write_manifest(tmp_path, body)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        # No SyntaxError: the triple-quote run was collapsed, docstring intact.
        compile(content, str(out), "exec")
        # No run of 3+ double-quotes survives inside the injected docstring text.
        doc_body = content.split('"""', 2)
        assert '"""' not in doc_body[1], "docstring delimiter re-opened by payload"

    # --- python raw-SDK template: same free-text slots must stay inert ---
    def test_python_raw_template_description_injection_compiles(self, tmp_path):
        body = (
            "name: pybot\n"
            "description: 'break \"\"\" MARKER_RAWDOC'\n"
            "runtime:\n  type: python\n  command: python.exe\n"
            "tools:\n"
            "  - name: ping\n"
            "    description: 'x\"; import os; os.system(\"MARKER_RAW\")  #'\n"
        )
        m = self._write_manifest(tmp_path, body)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        compile(content, str(out), "exec")

    # --- regression: a clean manifest still produces valid, expected output ---
    def test_clean_manifest_still_valid(self, tmp_path):
        m = load_manifest(FIXTURES / "minimal.yaml")
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        compile(content, str(out), "exec")
        assert 'name="ping"' in content
        assert 'Server("test-bot")' in content


class TestSecurityOutputPathTraversal:
    """P2: manifest.runtime.output must not escape the output dir."""

    def _manifest(self, output: str) -> Manifest:
        return Manifest(
            name="safe-server",
            description="d",
            runtime=RuntimeSpec.from_dict(
                {"type": "python", "command": "python.exe", "output": output}
            ),
            tools=[ToolSpec.from_dict({"name": "ping", "description": "p"})],
        )

    def test_parent_traversal_rejected(self, tmp_path):
        m = self._manifest("../../evil.py")
        with pytest.raises(ValueError, match="escapes|traversal|relative"):
            generate_server(m, tmp_path / "out")
        assert not (tmp_path / "evil.py").exists()

    def test_absolute_path_rejected(self, tmp_path):
        # Absolute path outside output_dir (works on POSIX and Windows).
        abs_target = tmp_path / "elsewhere" / "evil.py"
        m = self._manifest(str(abs_target))
        with pytest.raises(ValueError, match="absolute|relative"):
            generate_server(m, tmp_path / "out")
        assert not abs_target.exists()

    def test_normal_relative_path_still_works(self, tmp_path):
        out_dir = tmp_path / "out"
        m = self._manifest("sub/ping_server.py")
        result = generate_server(m, out_dir)
        assert result is not None and result.exists()
        # Written inside output_dir.
        assert str(out_dir.resolve()) in str(result.resolve())


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
