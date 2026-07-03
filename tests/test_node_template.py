"""Deliverable 4: Node server template tests.

Tests cover: template renders, generator dispatches on 'node' runtime,
output path uses .js extension, tool stubs present, and (if node available)
live integration with @modelcontextprotocol/sdk.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from mcp_factory.manifest import load_manifest
from mcp_factory.generator import generate_server, scaffold_summary

_EXAMPLES = Path(__file__).parent.parent / "examples"
_TEMPLATES = Path(__file__).parent.parent / "mcp_factory" / "templates"
_NODE_MODULES = Path(__file__).parent.parent / "node_modules"


@pytest.fixture
def node_manifest():
    return load_manifest(_EXAMPLES / "node_example.yaml")


class TestNodeTemplate:
    def test_template_file_exists(self):
        assert (_TEMPLATES / "node_server.js.j2").exists()

    def test_node_manifest_loads(self, node_manifest):
        assert node_manifest.runtime.type == "node"
        assert node_manifest.name == "node-example"

    def test_scaffold_summary_uses_js_extension(self, node_manifest, tmp_path):
        summary = scaffold_summary(node_manifest, tmp_path)
        assert summary["action"] == "scaffold_new"
        assert summary["output_path"].endswith(".js")

    def test_generate_node_server_creates_js(self, node_manifest, tmp_path):
        out = generate_server(node_manifest, tmp_path, force=True)
        assert out is not None
        assert out.suffix == ".js"
        assert out.exists()

    def test_generated_js_has_mcp_require(self, node_manifest, tmp_path):
        out = generate_server(node_manifest, tmp_path, force=True)
        content = out.read_text()
        assert "@modelcontextprotocol/sdk" in content

    def test_generated_js_has_all_tools(self, node_manifest, tmp_path):
        out = generate_server(node_manifest, tmp_path, force=True)
        content = out.read_text()
        for tool in node_manifest.tools:
            assert f'"{tool.name}"' in content or f"'{tool.name}'" in content

    def test_generated_js_has_zod(self, node_manifest, tmp_path):
        out = generate_server(node_manifest, tmp_path, force=True)
        assert "zod" in out.read_text()

    def test_generate_python_still_works(self, tmp_path):
        python_manifest = load_manifest(_EXAMPLES / "fleet_health.yaml")
        # fleet_health has existing script — generate_server returns None
        result = generate_server(python_manifest, tmp_path)
        assert result is None  # references existing script

    def test_generate_node_no_overwrite(self, node_manifest, tmp_path):
        generate_server(node_manifest, tmp_path, force=True)
        with pytest.raises(FileExistsError):
            generate_server(node_manifest, tmp_path, force=False)

    def test_generate_node_force_overwrites(self, node_manifest, tmp_path):
        first = generate_server(node_manifest, tmp_path, force=True)
        second = generate_server(node_manifest, tmp_path, force=True)
        assert first == second

    def test_generate_node_dry_run(self, node_manifest, tmp_path, capsys):
        result = generate_server(node_manifest, tmp_path, dry_run=True)
        assert result is None
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()

    @pytest.mark.skipif(
        not shutil.which("node") or not _NODE_MODULES.exists(),
        reason="node or @modelcontextprotocol/sdk not available",
    )
    def test_node_server_starts_and_initializes(self, node_manifest, tmp_path):
        """Integration: generated server starts and responds to initialize."""
        out = generate_server(node_manifest, tmp_path, force=True)
        assert out is not None

        proc = subprocess.Popen(
            ["node", str(out)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent),  # so require() finds node_modules
        )
        time.sleep(0.3)

        try:
            msg = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0.1"},
                },
            }
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            proc.stdin.flush()

            raw = proc.stdout.readline()
            resp = json.loads(raw.decode().strip())
            assert "error" not in resp
            assert resp["result"]["serverInfo"]["name"] == "node-example"
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)
