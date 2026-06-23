"""Regression tests for python_server.py.j2 — Windows-path backslash handling."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mcp_factory.generator import generate_server
from mcp_factory.manifest import load_manifest


def _windows_path_manifest(tmp_path: Path, command: str) -> Path:
    text = textwrap.dedent(f"""\
        name: windows-path-bot
        description: Bot with Windows-style interpreter path.
        runtime:
          type: python
          command: "{command}"
        tools:
          - name: ping
            description: Pong.
            args: []
    """)
    mf = tmp_path / "win_bot.yaml"
    mf.write_text(text, encoding="utf-8")
    return mf


class TestPythonTemplateWindowsPath:
    """Regression: \\U in runtime.command must not cause SyntaxError in generated stub."""

    def test_users_path_compiles(self, tmp_path):
        """C:\\Users\\... path — \\U triggers unicode escape in plain strings."""
        mf = _windows_path_manifest(
            tmp_path,
            r"C:\\Users\\owner\\projects\\mybot\\.venv\\Scripts\\python.exe",
        )
        m = load_manifest(mf)
        out = generate_server(m, tmp_path)
        assert out is not None
        content = out.read_text(encoding="utf-8")
        # Must not raise SyntaxError
        compile(content, str(out), "exec")

    def test_backslash_u_in_path_does_not_raise(self, tmp_path):
        """Specifically the \\U escape sequence that triggered the original bug."""
        mf = _windows_path_manifest(tmp_path, r"C:\\Users\\foo\\python.exe")
        m = load_manifest(mf)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        try:
            compile(content, str(out), "exec")
        except SyntaxError as exc:
            pytest.fail(f"Generated stub has SyntaxError from \\U in path: {exc}")

    def test_generated_stub_is_valid_ast(self, tmp_path):
        """Full AST parse — catches any escape-related syntax issue."""
        import ast

        mf = _windows_path_manifest(
            tmp_path,
            r"C:\\Users\\owner\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
        )
        m = load_manifest(mf)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        # ast.parse raises SyntaxError on invalid Python
        ast.parse(content)

    def test_docstring_contains_raw_prefix(self, tmp_path):
        """Generated file must use r-string docstring so paths are safe."""
        mf = _windows_path_manifest(tmp_path, r"C:\\Users\\owner\\python.exe")
        m = load_manifest(mf)
        out = generate_server(m, tmp_path)
        content = out.read_text(encoding="utf-8")
        assert content.startswith('#!/usr/bin/env python3\nr"""'), (
            "Module docstring must be a raw string (r\"\"\") to protect Windows paths"
        )
