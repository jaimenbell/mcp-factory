#!/usr/bin/env python3
"""Full integration smoke test — calls EVERY tool the hub exposes.

Spawns hub_server.py --serve, lists all registered tools via `tools/list`, then
calls each one with empty arguments. After a timeout the hub is killed and
restarted so subsequent calls are not poisoned by a hung subprocess.

The tool roster is discovered dynamically — nothing is hardcoded — so this works
against whatever bots/manifests exist under the configured scan roots.

Status codes:
  OK        — valid JSON returned, no "error" key at top level
  TOOL_ERR  — {"error": ...} (stub, missing state file, or no running bot)
  TIMEOUT   — hub didn't respond within CALL_TIMEOUT_S (likely live API call)
  RPC_ERR   — JSON-RPC level error (subprocess crash, routing failure)

Usage:
    # Set scan roots that contain mcp.yaml manifests (os.pathsep-separated):
    python scripts/smoke_test_all_tools.py --scan-root /path/to/projects
    python scripts/smoke_test_all_tools.py --scan-root /path/to/projects --markdown
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_HUB_PY = Path(__file__).parent.parent / "hub_server.py"
CALL_TIMEOUT_S = 12   # max per tool call; hub restarted after timeout


# ── Hub session (restartable) ─────────────────────────────────────────────────

class HubSession:
    def __init__(self, cmd: list[str]) -> None:
        self._cmd = cmd
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._poisoned = True  # force start on first call

    def ensure_ready(self) -> None:
        if self._poisoned or self._proc is None or self._proc.poll() is not None:
            self._kill()
            self._start()

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.8)
        self._next_id = 0
        resp = self._rpc_raw("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-all", "version": "1.0"},
        }, timeout=15)
        if resp is None or "error" in resp:
            raise RuntimeError(f"Hub init failed: {resp}")
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._poisoned = False

    def _kill(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
        self._poisoned = True

    def list_tools(self) -> list[str]:
        self.ensure_ready()
        resp = self._rpc_raw("tools/list", {}, timeout=15)
        if not resp or "error" in resp:
            raise RuntimeError(f"tools/list failed: {resp}")
        tools = resp.get("result", {}).get("tools", [])
        return [t["name"] for t in tools]

    def call(self, tool: str, args: dict, timeout: float = CALL_TIMEOUT_S) -> tuple[str, str]:
        """Returns (status, detail)."""
        self.ensure_ready()
        try:
            resp = self._rpc_raw("tools/call", {"name": tool, "arguments": args}, timeout=timeout)
        except TimeoutError:
            self._poisoned = True
            return "TIMEOUT", f"no response in {timeout:.0f}s"
        except Exception as exc:
            self._poisoned = True
            return "RPC_ERR", str(exc)[:100]

        if resp is None:
            self._poisoned = True
            return "RPC_ERR", "hub closed stdout"
        if "error" in resp:
            return "RPC_ERR", str(resp["error"])[:100]

        content = resp.get("result", {}).get("content", [])
        text = content[0]["text"] if content else ""
        try:
            nested = json.loads(text) if text.startswith(("{", "[")) else None
        except json.JSONDecodeError:
            nested = None

        if isinstance(nested, dict) and "error" in nested:
            return "TOOL_ERR", str(nested["error"])[:100]
        if isinstance(nested, dict) and nested.get("status") == "not_implemented":
            return "TOOL_ERR", f"not_implemented: {nested.get('reason','')[:70]}"
        return "OK", text[:80]

    def shutdown(self) -> None:
        self._kill()

    # ── low-level ─────────────────────────────────────────────────────────────

    def _write(self, msg: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("No process")
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        self._proc.stdin.flush()

    def _rpc_raw(self, method: str, params: Any, timeout: float) -> dict | None:
        self._next_id += 1
        req_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        result_q: queue.Queue = queue.Queue()

        def _reader():
            while True:
                try:
                    assert self._proc and self._proc.stdout
                    raw = self._proc.stdout.readline()
                except Exception:
                    result_q.put(None)
                    return
                if not raw:
                    result_q.put(None)
                    return
                text = raw.decode().strip()
                if not text:
                    continue
                try:
                    resp = json.loads(text)
                    if resp.get("id") == req_id:
                        result_q.put(resp)
                        return
                except json.JSONDecodeError:
                    pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            raise TimeoutError(f"No response in {timeout}s")
        try:
            return result_q.get_nowait()
        except queue.Empty:
            return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan-root", action="append", default=None,
                   help="Manifest scan root (repeatable). "
                        "Defaults to $MCP_FACTORY_SMOKE_ROOTS (os.pathsep-separated).")
    p.add_argument("--markdown", action="store_true")
    args = p.parse_args(argv)

    scan_roots = args.scan_root or [
        r for r in os.environ.get("MCP_FACTORY_SMOKE_ROOTS", "").split(os.pathsep) if r
    ]

    cmd = [sys.executable, str(_HUB_PY), "--serve"]
    for root in scan_roots:
        cmd += ["--scan-root", root]

    hub = HubSession(cmd)
    results: list[tuple[str, str, str, int]] = []  # (tool, status, detail, ms)

    try:
        all_tools = [t for t in hub.list_tools() if not t.startswith("_hub")]
        if not all_tools:
            print("[smoke] no bot tools discovered — pass --scan-root or set "
                  "MCP_FACTORY_SMOKE_ROOTS to a path with mcp.yaml manifests.")
            return 0
        for tool_qname in all_tools:
            t0 = time.monotonic()
            status, detail = hub.call(tool_qname, {})
            ms = round((time.monotonic() - t0) * 1000)
            results.append((tool_qname, status, detail, ms))
            if not args.markdown:
                icon = {"OK": "OK  ", "TOOL_ERR": "FERR", "TIMEOUT": "TIME", "RPC_ERR": "RERR"}.get(status, status)
                print(f"  [{icon}] {tool_qname:<52s} {ms:6d}ms  {detail[:55]}", flush=True)
    finally:
        hub.shutdown()

    ok_count    = sum(1 for _, s, _, _ in results if s == "OK")
    tool_errors = [(t, d) for t, s, d, _ in results if s == "TOOL_ERR"]
    timeouts    = [(t, d) for t, s, d, _ in results if s == "TIMEOUT"]
    rpc_errors  = [(t, d) for t, s, d, _ in results if s == "RPC_ERR"]
    total       = len(results)

    if args.markdown:
        print("## Hub Tool Smoke Test\n")
        print(f"**{ok_count}/{total} OK** | "
              f"TOOL\\_ERR: {len(tool_errors)} | "
              f"TIMEOUT: {len(timeouts)} | "
              f"RPC\\_ERR: {len(rpc_errors)}\n")
        print("> TOOL_ERR = tool body ran but state/cache file missing (no running bot — expected)  ")
        print("> TIMEOUT = tool tried live API call with no running bot — expected  ")
        print("> RPC_ERR = routing or subprocess failure — real bug\n")
        print("| # | Tool | Status | ms | Detail |")
        print("|---|------|--------|----|--------|")
        for i, (tool, status, detail, ms) in enumerate(results, 1):
            icons = {"OK": "[OK]", "TOOL_ERR": "[FERR]", "TIMEOUT": "[TIME]", "RPC_ERR": "[RERR]"}
            icon = icons.get(status, "[?]")
            safe = detail.replace("|", "\\|")[:65]
            print(f"| {i} | `{tool}` | {icon} {status} | {ms} | {safe} |")
    else:
        print(f"\n[smoke] {ok_count}/{total} OK | "
              f"TOOL_ERR={len(tool_errors)} TIMEOUT={len(timeouts)} RPC_ERR={len(rpc_errors)}")
        if rpc_errors:
            print("[smoke] RPC_ERR (real bugs — routing/subprocess failures):")
            for t, d in rpc_errors:
                print(f"  {t}: {d[:80]}")

    return 0 if not rpc_errors else 1


if __name__ == "__main__":
    sys.exit(main())
