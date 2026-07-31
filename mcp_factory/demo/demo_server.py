#!/usr/bin/env python3
"""Bundled DEMO MCP server for mcp-factory's directory-listing fallback.

This is NOT a real integration. It exists solely so a fresh install (or a
scanning sandbox with no other bots configured) shows a working example
instead of serving zero tools. See mcp_factory/demo/mcp.yaml for the
manifest and mcp_factory/cli.py::_run_serve for exactly when this fires.

Stdlib only, no dependencies. Speaks newline-delimited JSON-RPC 2.0 over
stdin/stdout -- the same protocol mcp_factory.runtime.subprocess_adapter
expects (initialize + tools/call; tools/list is not required because the
hub builds its tool listing from the manifest directly, never from the
subprocess).
"""
from __future__ import annotations

import json
import sys


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")

        # Notifications have no id -- no response needed.
        if msg_id is None:
            continue

        if method == "initialize":
            _write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-factory-demo", "version": "0.1.0"},
                },
            })

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "say_hello":
                who = arguments.get("name") or "there"
                text = (
                    f"[DEMO] Hello, {who}! This is mcp-factory-demo, a bundled "
                    "example bot -- not a real integration. It only ever serves "
                    "when no real mcp.yaml manifests are found (set "
                    "MCP_FACTORY_NO_DEMO=1 or pass --no-demo to disable it)."
                )
                _write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                })
            else:
                _write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                })

        else:
            _write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            })


if __name__ == "__main__":
    main()
