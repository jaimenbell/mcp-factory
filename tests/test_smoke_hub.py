"""Smoke-test the hub via subprocess JSON-RPC (real integration test).

Fleet-agnostic: discovers whatever bots exist under the configured scan roots
and asserts structural invariants (no hardcoded roster or counts). The module
skips automatically when no bots are discovered — e.g. a fresh checkout or CI
where no `mcp.yaml` manifests are present.

To exercise it against your own bots, set MCP_FACTORY_SMOKE_ROOTS to one or
more manifest scan roots (os.pathsep-separated), e.g.:

    MCP_FACTORY_SMOKE_ROOTS="C:\\Users\\me\\projects" pytest tests/test_smoke_hub.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HUB_PY = Path(__file__).parent.parent / "hub_server.py"
_SCAN_ROOTS = [r for r in os.environ.get("MCP_FACTORY_SMOKE_ROOTS", "").split(os.pathsep) if r]


def _start_hub() -> subprocess.Popen:
    cmd = [sys.executable, str(_HUB_PY), "--serve"]
    for root in _SCAN_ROOTS:
        cmd += ["--scan-root", root]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _rpc(proc: subprocess.Popen, method: str, params, id_: int) -> dict:
    msg = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    while True:
        raw = proc.stdout.readline()
        if not raw:
            stderr = proc.stderr.read(512).decode(errors="replace")
            raise RuntimeError(f"Hub stdout closed. stderr={stderr!r}")
        txt = raw.decode().strip()
        if txt:
            resp = json.loads(txt)
            if resp.get("id") == id_:
                return resp


def _initialize(hub):
    resp = _rpc(
        hub,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest-smoke", "version": "0.1"},
        },
        id_=1,
    )
    hub.stdin.write(
        (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
    )
    hub.stdin.flush()
    return resp


@pytest.fixture(scope="module")
def hub():
    proc = _start_hub()
    time.sleep(0.5)
    try:
        _initialize(proc)
        resp = _rpc(proc, "tools/call", {"name": "_hub.list_bots", "arguments": {}}, id_=900)
        bots = json.loads(resp["result"]["content"][0]["text"])
        if not bots:
            pytest.skip("no bots discovered — set MCP_FACTORY_SMOKE_ROOTS to a scan root with manifests")
        yield proc
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)


class TestHubSmoke:
    def test_initialize_succeeds(self, hub):
        # hub fixture already initialized; re-list to confirm server identity is stable
        resp = _rpc(hub, "tools/list", {}, id_=2)
        assert "error" not in resp

    def test_tools_list_has_hub_meta_tool(self, hub):
        resp = _rpc(hub, "tools/list", {}, id_=3)
        names = {t["name"] for t in resp["result"]["tools"]}
        assert "_hub.list_bots" in names

    def test_bot_tools_are_namespaced_with_dot(self, hub):
        resp = _rpc(hub, "tools/list", {}, id_=5)
        bot_tools = [t["name"] for t in resp["result"]["tools"] if not t["name"].startswith("_hub")]
        assert bot_tools, "expected at least one bot tool from discovered manifests"
        for name in bot_tools:
            assert "." in name, f"Tool {name!r} missing dot namespace"

    def test_discovered_bot_stubs_respond(self, hub):
        """Each discovered bot's health tool must respond without a missing-file or syntax error."""
        resp = _rpc(hub, "tools/call", {"name": "_hub.list_bots", "arguments": {}}, id_=6)
        bots = json.loads(resp["result"]["content"][0]["text"])
        list_resp = _rpc(hub, "tools/list", {}, id_=7)
        tool_names = {t["name"] for t in list_resp["result"]["tools"]}

        for bot_name in bots:
            health_tool = f"{bot_name}.get_bot_health"
            if health_tool not in tool_names:
                continue  # not every bot exposes get_bot_health
            r = _rpc(hub, "tools/call", {"name": health_tool, "arguments": {}}, id_=101)
            if "error" in r:
                msg = json.dumps(r["error"])
            else:
                content = r.get("result", {}).get("content", [])
                msg = content[0]["text"] if content else ""
            assert "can't open file" not in msg, f"{bot_name}: stub file missing: {msg[:300]}"
            assert "No such file" not in msg, f"{bot_name}: stub file missing: {msg[:300]}"
            assert "SyntaxError" not in msg, f"{bot_name}: stub has syntax error: {msg[:300]}"
