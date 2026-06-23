#!/usr/bin/env python3
"""Smoke-test the hub end-to-end via subprocess JSON-RPC.

Spawns hub_server.py --serve, sends initialize + tools/list, then calls one
read-only health tool per discovered bot. Documents errors without failing —
bot wrappers (mcp_server.py) may be missing at this stage.

The bot roster is discovered dynamically via `_hub.list_bots` — nothing is
hardcoded — so this works against whatever manifests exist under the scan roots.

Usage:
    python scripts/smoke_test_hub.py --scan-root /path/to/projects
    # or set $MCP_FACTORY_SMOKE_ROOTS (os.pathsep-separated)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_HUB_PY = Path(__file__).parent.parent / "hub_server.py"


def _rpc(proc: subprocess.Popen, method: str, params: Any, id_: int) -> dict:
    msg = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()

    while True:
        raw = proc.stdout.readline()
        if not raw:
            stderr = proc.stderr.read(512).decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub closed stdout. stderr: {stderr!r}")
        text = raw.decode().strip()
        if text:
            resp = json.loads(text)
            if resp.get("id") == id_:
                return resp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-root", action="append", default=None,
                    help="Manifest scan root (repeatable). "
                         "Defaults to $MCP_FACTORY_SMOKE_ROOTS (os.pathsep-separated).")
    args = ap.parse_args(argv)
    scan_roots = args.scan_root or [
        r for r in os.environ.get("MCP_FACTORY_SMOKE_ROOTS", "").split(os.pathsep) if r
    ]

    cmd = [sys.executable, str(_HUB_PY), "--serve"]
    for root in scan_roots:
        cmd += ["--scan-root", root]

    print(f"[smoke] Starting hub: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    time.sleep(0.5)  # let hub boot and scan manifests

    results: dict[str, str] = {}
    try:
        # 1. initialize
        resp = _rpc(
            proc,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "0.1"},
            },
            id_=1,
        )
        if "error" in resp:
            print(f"[smoke] FAIL initialize: {resp['error']}")
            return 1
        server_name = resp.get("result", {}).get("serverInfo", {}).get("name", "?")
        print(f"[smoke] OK   initialize -> serverInfo.name={server_name!r}")

        notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        proc.stdin.write(notif.encode())
        proc.stdin.flush()

        # 2. tools/list
        resp = _rpc(proc, "tools/list", {}, id_=2)
        if "error" in resp:
            print(f"[smoke] FAIL tools/list: {resp['error']}")
            return 1
        tools = resp.get("result", {}).get("tools", [])
        tool_names = {t["name"] for t in tools}
        bot_tools = [n for n in tool_names if not n.startswith("_hub")]
        meta_tools = [n for n in tool_names if n.startswith("_hub")]
        print(f"[smoke] OK   tools/list -> {len(bot_tools)} bot tools + {len(meta_tools)} meta")
        assert "_hub.list_bots" in tool_names, "_hub.list_bots missing"

        # 3. _hub.list_bots — discover the roster dynamically
        resp = _rpc(proc, "tools/call", {"name": "_hub.list_bots", "arguments": {}}, id_=3)
        bots: dict[str, Any] = {}
        if "error" in resp:
            print(f"[smoke] FAIL _hub.list_bots: {resp['error']}")
        else:
            content = resp.get("result", {}).get("content", [])
            text = content[0]["text"] if content else "{}"
            bots = json.loads(text)
            print(f"[smoke] OK   _hub.list_bots -> {list(bots.keys())}")

        if not bots:
            print("[smoke] no bots discovered — pass --scan-root or set "
                  "MCP_FACTORY_SMOKE_ROOTS to a path with mcp.yaml manifests.")
            return 0

        # 4. One read-only health call per discovered bot
        print("\n[smoke] Calling one read-only tool per bot:")
        call_id = 4
        for bot_name in bots:
            tool_qname = f"{bot_name}.get_bot_health"
            if tool_qname not in tool_names:
                # fall back to the first tool exposed by this bot
                candidates = [n for n in bot_tools if n.startswith(f"{bot_name}.")]
                if not candidates:
                    results[bot_name] = "SKIP: no tools"
                    continue
                tool_qname = candidates[0]
            resp = _rpc(proc, "tools/call", {"name": tool_qname, "arguments": {}}, id_=call_id)
            call_id += 1
            if "error" in resp:
                err_msg = resp["error"].get("message", str(resp["error"]))
                print(f"  [ERR]  {bot_name}: {err_msg[:120]}")
                results[bot_name] = f"ERROR: {err_msg[:80]}"
            else:
                content = resp.get("result", {}).get("content", [])
                full_text = content[0]["text"] if content else "(empty)"
                text = full_text[:80]
                try:
                    nested = json.loads(full_text) if full_text.startswith("{") else None
                except json.JSONDecodeError:
                    nested = None
                if nested and "error" in nested:
                    print(f"  [FAIL] {bot_name}: {str(nested['error'])[:120]}")
                    results[bot_name] = f"TOOL_ERROR: {str(nested['error'])[:80]}"
                else:
                    print(f"  [OK]   {bot_name}: {text[:80]}")
                    results[bot_name] = "OK"

    finally:
        proc.stdin.close()
        proc.wait(timeout=5)

    # Summary
    print("\n[smoke] Summary:")
    ok = [b for b, r in results.items() if r == "OK"]
    err = {b: r for b, r in results.items() if r != "OK"}
    print(f"  OK:     {ok}")
    print(f"  Errors: {len(err)}")
    for b, r in err.items():
        print(f"    {b}: {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
