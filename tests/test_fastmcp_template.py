"""FastMCP-style Python template tests.

Mirrors tests/test_node_template.py's structure: template renders, generator
dispatches on runtime.style == "fastmcp", output stays a real generated
scaffold, and (if fastmcp is installed) a live import + subprocess smoke test
proves the generated server actually speaks MCP over stdio.

The raw-SDK python_server.py.j2 template and its pinned tests
(test_python_template.py, test_generator.py) are untouched — this file only
covers the new fastmcp variant.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_factory.manifest import load_manifest
from mcp_factory.generator import generate_server

_EXAMPLES = Path(__file__).parent.parent / "examples"
_TEMPLATES = Path(__file__).parent.parent / "templates"

_FASTMCP_AVAILABLE = importlib.util.find_spec("fastmcp") is not None


@pytest.fixture
def fastmcp_manifest():
    return load_manifest(_EXAMPLES / "fastmcp_example.yaml")


def _windows_safe_python_command() -> str:
    """sys.executable with backslashes doubled, safe to embed in a YAML double-quoted string."""
    return sys.executable.replace("\\", "\\\\")


class TestFastmcpTemplateSelection:
    def test_template_file_exists(self):
        assert (_TEMPLATES / "python_fastmcp.j2").exists()

    def test_fastmcp_manifest_loads(self, fastmcp_manifest):
        assert fastmcp_manifest.runtime.type == "python"
        assert fastmcp_manifest.runtime.style == "fastmcp"
        assert fastmcp_manifest.name == "fastmcp-example"

    def test_default_style_is_raw(self, tmp_path):
        """A manifest with no runtime.style keeps generating the raw-SDK template."""
        text = textwrap.dedent("""\
            name: raw-default-bot
            description: No style set - must default to raw.
            runtime:
              type: python
              command: python.exe
            tools:
              - name: ping
                description: Pong.
                args: []
        """)
        mf = tmp_path / "raw_default.yaml"
        mf.write_text(text)
        m = load_manifest(mf)
        assert m.runtime.style == "raw"
        out = generate_server(m, tmp_path)
        content = out.read_text()
        assert "from mcp.server import Server" in content
        assert "from fastmcp import FastMCP" not in content

    def test_invalid_style_raises(self, tmp_path):
        text = textwrap.dedent("""\
            name: bad-style-bot
            description: Invalid style value.
            runtime:
              type: python
              command: python.exe
              style: django
            tools: []
        """)
        mf = tmp_path / "bad_style.yaml"
        mf.write_text(text)
        with pytest.raises(ValueError, match="style"):
            load_manifest(mf)


class TestFastmcpTemplateGeneration:
    def test_generate_creates_file(self, fastmcp_manifest, tmp_path):
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        assert out is not None
        assert out.exists()
        assert out.suffix == ".py"

    def test_generated_file_has_fastmcp_import(self, fastmcp_manifest, tmp_path):
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        content = out.read_text()
        assert "from fastmcp import FastMCP" in content
        assert 'FastMCP(SERVER_NAME)' in content

    def test_generated_file_has_all_tools(self, fastmcp_manifest, tmp_path):
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        content = out.read_text()
        for tool in fastmcp_manifest.tools:
            assert f'name="{tool.name}"' in content

    def test_generated_file_is_valid_ast(self, fastmcp_manifest, tmp_path):
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        ast.parse(out.read_text())

    def test_generated_file_compiles(self, fastmcp_manifest, tmp_path):
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        compile(out.read_text(), str(out), "exec")

    def test_generated_file_has_fail_soft_wrapper(self, fastmcp_manifest, tmp_path):
        """Every tool body is wrapped so a runtime exception can't crash the server."""
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        content = out.read_text()
        assert "fail-soft" in content.lower()
        assert content.count("except Exception as exc:") == len(fastmcp_manifest.tools)
        assert '"status": "error"' in content

    def test_generated_file_has_env_required_check(self, fastmcp_manifest, tmp_path):
        """manifest.env_required is rendered into a startup scoping gate."""
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        content = out.read_text()
        assert "_check_required_env" in content
        assert '"EXAMPLE_API_KEY"' in content
        assert "_check_required_env()" in content  # actually called at __main__

    def test_generated_file_has_no_required_env_when_manifest_omits_it(self, tmp_path):
        text = textwrap.dedent("""\
            name: no-env-bot
            description: No env_required set.
            runtime:
              type: python
              command: python.exe
              style: fastmcp
            tools:
              - name: ping
                description: Pong.
                args: []
        """)
        mf = tmp_path / "no_env.yaml"
        mf.write_text(text)
        m = load_manifest(mf)
        out = generate_server(m, tmp_path)
        content = out.read_text()
        assert "REQUIRED_ENV_VARS = []" in content

    def test_generated_file_pins_fastmcp_version(self, fastmcp_manifest, tmp_path):
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        content = out.read_text()
        assert "fastmcp>=3.4.2" in content

    def test_arg_schema_fidelity_required_and_optional(self, fastmcp_manifest, tmp_path):
        """Required args have no default; optional args default to None via Optional[...]."""
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        content = out.read_text()
        # message: optional string on `ping`
        assert "message: Annotated[Optional[str], Field(description=" in content
        # payload: required object on `echo_json`
        assert "payload: Annotated[dict, Field(description=" in content

    def test_no_overwrite_without_force(self, fastmcp_manifest, tmp_path):
        generate_server(fastmcp_manifest, tmp_path, force=True)
        with pytest.raises(FileExistsError):
            generate_server(fastmcp_manifest, tmp_path, force=False)

    def test_dry_run_no_file_written(self, fastmcp_manifest, tmp_path, capsys):
        result = generate_server(fastmcp_manifest, tmp_path, dry_run=True)
        assert result is None
        assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not _FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestFastmcpTemplateImport:
    """Actually import the generated module and exercise the real FastMCP app object."""

    def test_import_registers_tools(self, fastmcp_manifest, tmp_path):
        import asyncio

        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        spec = importlib.util.spec_from_file_location("fastmcp_example_server_under_test", out)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        async def _list():
            return await mod.mcp.list_tools()

        tools = asyncio.run(_list())
        names = {t.name for t in tools}
        assert names == {"ping", "get_status", "echo_json"}

    def test_tool_call_returns_not_implemented_stub(self, fastmcp_manifest, tmp_path):
        import asyncio

        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        spec = importlib.util.spec_from_file_location("fastmcp_example_server_under_test2", out)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = asyncio.run(mod.get_status())
        assert result == {"status": "not_implemented", "tool": "get_status"}

    def test_check_required_env_warns_on_missing(self, fastmcp_manifest, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("EXAMPLE_API_KEY", raising=False)
        out = generate_server(fastmcp_manifest, tmp_path, force=True)
        spec = importlib.util.spec_from_file_location("fastmcp_example_server_under_test3", out)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mod._check_required_env()
        captured = capsys.readouterr()
        assert "EXAMPLE_API_KEY" in captured.err
        assert "WARNING" in captured.err


@pytest.mark.skipif(not _FASTMCP_AVAILABLE, reason="fastmcp not installed")
class TestFastmcpServeSmoke:
    """End-to-end: generate -> spawn as a real subprocess -> speak MCP over stdio.

    Uses the same SubprocessAdapter the hub uses for the raw-SDK template,
    proving the fastmcp-generated server is wire-compatible with the existing
    runtime hub without any adapter changes.
    """

    def test_fastmcp_server_answers_tool_call_over_stdio(self, tmp_path):
        from mcp_factory.runtime.subprocess_adapter import SubprocessAdapter

        manifest_text = textwrap.dedent(f"""\
            name: fastmcp-smoke
            description: Smoke-test fastmcp server.
            runtime:
              type: python
              command: "{_windows_safe_python_command()}"
              style: fastmcp
            tools:
              - name: echo
                description: Echo a message.
                args:
                  - name: message
                    type: string
                    required: true
                    description: message to echo
        """)
        mf = tmp_path / "smoke.yaml"
        mf.write_text(manifest_text)
        m = load_manifest(mf)
        out = generate_server(m, tmp_path, force=True)
        m.runtime.output = str(out)

        adapter = SubprocessAdapter(m)
        try:
            result = adapter.call_tool("echo", {"message": "hello smoke test"})
            assert result["isError"] is False
            text = result["content"][0]["text"]
            assert "not_implemented" in text
            assert "echo" in text
        finally:
            adapter.stop()
