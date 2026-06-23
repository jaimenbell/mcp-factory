"""Hub MCP server — exposes all registered bots' tools under <bot>.<tool> namespacing.

Start with:  python hub_server.py --serve [--scan-root <root>]

Subprocess lifecycle:
  - Adapters are lazy-started on first tool call.
  - All adapters are stopped on hub exit (atexit + signal handlers).
  - The meta-tool _hub.list_bots returns registered bots and subprocess status.
"""
from __future__ import annotations

import atexit
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp_factory.manifest import Manifest
from mcp_factory.runtime.registry import Registry


def _require_mcp() -> Any:
    try:
        import mcp  # noqa: F401
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp import types
        return Server, stdio_server, types
    except ImportError:
        print(
            "[hub] ERROR: 'mcp' package not installed. "
            "Run: pip install mcp>=1.0",
            file=sys.stderr,
        )
        sys.exit(1)


async def run_hub(manifests: list[Manifest]) -> None:
    """Async entry point for the hub MCP server."""
    Server, stdio_server, types = _require_mcp()

    registry = Registry()
    errors: list[str] = []

    for m in manifests:
        try:
            registry.register_manifest(m)
        except Exception as exc:  # CollisionError or anything else
            errors.append(f"  skip '{m.name}': {exc}")

    if errors:
        print("[hub] Manifest registration warnings:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)

    # Ensure subprocess cleanup on exit
    atexit.register(registry.shutdown)

    tool_count = len(registry.list_tools())
    bot_count = len(registry.registered_bots())
    print(
        f"[hub] Ready — {bot_count} bots, {tool_count} tools (+_hub.list_bots)",
        file=sys.stderr,
    )

    server = Server("mcp-factory-hub")

    @server.list_tools()
    async def list_tools() -> list[Any]:  # list[types.Tool]
        result = [
            types.Tool(
                name="_hub.list_bots",
                description=(
                    "List all bots registered in the hub and their subprocess status. "
                    "Returns a JSON object keyed by bot name."
                ),
                inputSchema={"type": "object", "properties": {}, "required": []},
            )
        ]
        for entry in registry.list_tools():
            result.append(
                types.Tool(
                    name=entry.qualified_name,
                    description=f"[{entry.bot_name}] {entry.tool_spec.description}",
                    inputSchema=entry.tool_spec.to_input_schema(),
                )
            )
        return result

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        if name == "_hub.list_bots":
            registry.reap_idle()
            payload: dict[str, Any] = {}
            for bot in registry.registered_bots():
                tools = [e.tool_name for e in registry.list_tools() if e.bot_name == bot]
                payload[bot] = {
                    "tools": tools,
                    "status": registry.adapter_status(bot),
                }
            return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

        entry = registry.get_tool(name)
        if entry is None:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"error": f"Unknown tool: {name!r}"}),
                )
            ]

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: registry.call_tool(name, arguments or {}),
            )
        except Exception as exc:
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps({"error": str(exc), "tool": name}),
                )
            ]

        # Forward content from the subprocess response
        content_items = result.get("content", [])
        if not content_items:
            return [types.TextContent(type="text", text=json.dumps(result))]

        out: list[Any] = []
        for item in content_items:
            if item.get("type") == "text":
                out.append(types.TextContent(type="text", text=item["text"]))
            else:
                out.append(types.TextContent(type="text", text=json.dumps(item)))
        return out

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
