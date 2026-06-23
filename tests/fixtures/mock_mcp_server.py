#!/usr/bin/env python3
"""Minimal MCP server for subprocess adapter tests.

No external dependencies — uses only stdlib.  Implements the JSON-RPC 2.0
over newline-delimited stdio protocol expected by SubprocessAdapter.

Tools exposed:
  ping   — returns {"pong": true}
  echo   — returns {"echoed": <message arg>}
  crash  — raises an error (for error-path tests)
"""
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

        # Notifications have no id — no response needed
        if msg_id is None:
            continue

        if method == "initialize":
            _write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock-server", "version": "0.1.0"},
                },
            })

        elif method == "tools/list":
            _write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "ping",
                            "description": "Ping the server",
                            "inputSchema": {"type": "object", "properties": {}, "required": []},
                        },
                        {
                            "name": "echo",
                            "description": "Echo a message",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                                "required": ["message"],
                            },
                        },
                        {
                            "name": "crash",
                            "description": "Always returns an error",
                            "inputSchema": {"type": "object", "properties": {}, "required": []},
                        },
                    ]
                },
            })

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            if tool_name == "ping":
                _write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": '{"pong": true}'}],
                        "isError": False,
                    },
                })
            elif tool_name == "echo":
                message = arguments.get("message", "")
                _write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"echoed": message})}],
                        "isError": False,
                    },
                })
            elif tool_name == "crash":
                _write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32000, "message": "Intentional crash for testing"},
                })
            else:
                _write({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                })

        else:
            # Unknown method
            _write({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            })


if __name__ == "__main__":
    main()
