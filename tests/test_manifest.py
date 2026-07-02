"""Tests for manifest loading and validation."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from mcp_factory.manifest import (
    Manifest,
    ArgSpec,
    ToolSpec,
    RuntimeSpec,
    load_manifest,
    VALID_PRIORITIES,
    VALID_RUNTIME_TYPES,
    VALID_ARG_TYPES,
    VALID_PYTHON_STYLES,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadManifest:
    def test_load_fleet_health(self):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        assert m.name == "fleet-health"
        assert m.priority == "high"
        assert m.runtime.type == "python"
        assert m.runtime.command == "C:\\Python314\\python.exe"
        assert len(m.tools) == 4
        assert m.tool_names == ["fleet_status", "bot_status", "recent_alerts", "dump_markdown_report"]

    def test_load_minimal(self):
        m = load_manifest(FIXTURES / "minimal.yaml")
        assert m.name == "test-bot"
        assert m.priority == "low"
        assert len(m.tools) == 1
        assert m.tools[0].name == "ping"
        assert m.tools[0].args == []

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_manifest(FIXTURES / "nonexistent.yaml")

    def test_missing_required_field_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(textwrap.dedent("""\
            name: missing-description
            runtime:
              type: python
              command: python.exe
            tools: []
        """))
        with pytest.raises(ValueError, match="description"):
            load_manifest(bad)

    def test_invalid_priority_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(textwrap.dedent("""\
            name: x
            description: test
            runtime:
              type: python
              command: python.exe
            tools: []
            priority: URGENT
        """))
        with pytest.raises(ValueError, match="priority"):
            load_manifest(bad)

    def test_invalid_runtime_type_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(textwrap.dedent("""\
            name: x
            description: test
            runtime:
              type: rust
              command: ./server
            tools: []
        """))
        with pytest.raises(ValueError, match="runtime.type"):
            load_manifest(bad)

    def test_runtime_style_defaults_to_raw(self, tmp_path):
        mf = tmp_path / "no_style.yaml"
        mf.write_text(textwrap.dedent("""\
            name: x
            description: test
            runtime:
              type: python
              command: python.exe
            tools: []
        """))
        m = load_manifest(mf)
        assert m.runtime.style == "raw"
        assert "raw" in VALID_PYTHON_STYLES and "fastmcp" in VALID_PYTHON_STYLES

    def test_runtime_style_fastmcp_accepted(self, tmp_path):
        mf = tmp_path / "fastmcp_style.yaml"
        mf.write_text(textwrap.dedent("""\
            name: x
            description: test
            runtime:
              type: python
              command: python.exe
              style: fastmcp
            tools: []
        """))
        m = load_manifest(mf)
        assert m.runtime.style == "fastmcp"

    def test_invalid_runtime_style_raises(self, tmp_path):
        bad = tmp_path / "bad_style.yaml"
        bad.write_text(textwrap.dedent("""\
            name: x
            description: test
            runtime:
              type: python
              command: python.exe
              style: django
            tools: []
        """))
        with pytest.raises(ValueError, match="runtime.style"):
            load_manifest(bad)

    def test_invalid_arg_type_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(textwrap.dedent("""\
            name: x
            description: test
            runtime:
              type: python
              command: python.exe
            tools:
              - name: foo
                description: bar
                args:
                  - name: baz
                    type: dict
        """))
        with pytest.raises(ValueError, match="type"):
            load_manifest(bad)

    def test_tool_names_property(self):
        m = load_manifest(FIXTURES / "fleet_health.yaml")
        assert set(m.tool_names) == {"fleet_status", "bot_status", "recent_alerts", "dump_markdown_report"}

    def test_env_defaults_empty(self):
        m = load_manifest(FIXTURES / "minimal.yaml")
        assert m.env == {}
        assert m.env_required == []
        assert m.tags == []


class TestArgSpec:
    def test_optional_arg_required_false(self):
        arg = ArgSpec.from_dict({"name": "hours", "type": "number", "required": False})
        assert arg.required is False

    def test_default_required_true(self):
        arg = ArgSpec.from_dict({"name": "x", "type": "string"})
        assert arg.required is True

    def test_to_json_schema_property(self):
        arg = ArgSpec.from_dict({"name": "n", "type": "number", "description": "count"})
        prop = arg.to_json_schema_property()
        assert prop == {"type": "number", "description": "count"}


class TestToolSpec:
    def test_input_schema_required_only_required_args(self):
        tool = ToolSpec.from_dict({
            "name": "recent_alerts",
            "description": "alerts",
            "args": [
                {"name": "hours", "type": "number", "required": False},
            ],
        })
        schema = tool.to_input_schema()
        assert schema["required"] == []
        assert "hours" in schema["properties"]

    def test_input_schema_mixed_args(self):
        tool = ToolSpec.from_dict({
            "name": "bot_status",
            "description": "status",
            "args": [
                {"name": "bot_name", "type": "string", "required": True},
            ],
        })
        schema = tool.to_input_schema()
        assert schema["required"] == ["bot_name"]


class TestRuntimeSpec:
    def test_has_existing_script_true(self):
        existing = (Path(__file__).parent / "fixtures" / "fleet_health_server.py").resolve()
        rt = RuntimeSpec.from_dict({
            "type": "python",
            "command": "C:\\Python314\\python.exe",
            "script": str(existing),
        })
        assert rt.has_existing_script is True

    def test_has_existing_script_false_when_missing(self, tmp_path):
        rt = RuntimeSpec.from_dict({
            "type": "python",
            "command": "python.exe",
            "script": str(tmp_path / "nonexistent.py"),
        })
        assert rt.has_existing_script is False

    def test_has_existing_script_false_when_no_script(self):
        rt = RuntimeSpec.from_dict({
            "type": "python",
            "command": "python.exe",
        })
        assert rt.has_existing_script is False
