"""ToolRegistry — maps <bot>.<tool> qualified names to manifests and subprocess adapters.

Naming convention: f"{manifest.name}.{tool.name}" e.g. "fleet-health.fleet_status".
Adapters are lazy-started on first call_tool(); shut them all down with shutdown().
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from mcp_factory.manifest import Manifest, ToolSpec
from mcp_factory.runtime.subprocess_adapter import SubprocessAdapter

_DEFAULT_IDLE_TIMEOUT_MIN = 15.0


@dataclass
class ToolEntry:
    bot_name: str
    tool_name: str
    qualified_name: str
    tool_spec: ToolSpec
    manifest: Manifest


class CollisionError(ValueError):
    """Two manifests expose the same qualified tool name."""


class Registry:
    """Holds all registered tools and owns their subprocess adapters."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._adapters: dict[str, SubprocessAdapter] = {}
        self._idle_timeout_sec: float = float(
            os.environ.get("HUB_IDLE_TIMEOUT_MIN", str(_DEFAULT_IDLE_TIMEOUT_MIN))
        ) * 60

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_manifest(self, manifest: Manifest) -> None:
        """Register all tools from a manifest.  Raises CollisionError on duplicates."""
        for tool in manifest.tools:
            qname = f"{manifest.name}.{tool.name}"
            if qname in self._tools:
                existing = self._tools[qname].manifest.source_path or self._tools[qname].manifest.name
                raise CollisionError(
                    f"Tool name collision: '{qname}' already registered from {existing!r}. "
                    "Rename the tool in one of the manifests."
                )
            self._tools[qname] = ToolEntry(
                bot_name=manifest.name,
                tool_name=tool.name,
                qualified_name=qname,
                tool_spec=tool,
                manifest=manifest,
            )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def list_tools(self) -> list[ToolEntry]:
        return list(self._tools.values())

    def get_tool(self, qualified_name: str) -> ToolEntry | None:
        return self._tools.get(qualified_name)

    def registered_bots(self) -> list[str]:
        seen: dict[str, None] = {}
        for entry in self._tools.values():
            seen[entry.bot_name] = None
        return list(seen.keys())

    # ------------------------------------------------------------------
    # Adapter lifecycle
    # ------------------------------------------------------------------

    def get_or_create_adapter(self, bot_name: str) -> SubprocessAdapter:
        """Return the adapter for bot_name, creating it if it doesn't exist yet."""
        if bot_name not in self._adapters:
            manifest = self._manifest_for_bot(bot_name)
            self._adapters[bot_name] = SubprocessAdapter(manifest)
        return self._adapters[bot_name]

    def adapter_status(self, bot_name: str) -> str:
        """Return 'running', 'idle', or 'not_registered'."""
        if bot_name not in self._tools and not any(
            e.bot_name == bot_name for e in self._tools.values()
        ):
            return "not_registered"
        adapter = self._adapters.get(bot_name)
        if adapter is None:
            return "idle"
        return "running" if adapter.is_alive else "stopped"

    def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Resolve qualified_name to an adapter and call the underlying tool."""
        entry = self._tools.get(qualified_name)
        if entry is None:
            raise KeyError(f"Unknown tool: '{qualified_name}'")
        adapter = self.get_or_create_adapter(entry.bot_name)
        return adapter.call_tool(entry.tool_name, arguments)

    def reap_idle(self) -> list[str]:
        """Stop adapters idle longer than HUB_IDLE_TIMEOUT_MIN. Returns reaped bot names."""
        reaped: list[str] = []
        for bot_name in list(self._adapters):
            adapter = self._adapters[bot_name]
            if adapter.is_alive and adapter.idle_seconds >= self._idle_timeout_sec:
                idle_min = adapter.idle_seconds / 60
                print(
                    f"[hub] Reaped {bot_name} subprocess after {idle_min:.1f} min idle",
                    file=sys.stderr,
                )
                adapter.stop()
                del self._adapters[bot_name]
                reaped.append(bot_name)
        return reaped

    def shutdown(self) -> None:
        """Stop all running subprocess adapters."""
        for adapter in self._adapters.values():
            adapter.stop()
        self._adapters.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _manifest_for_bot(self, bot_name: str) -> Manifest:
        for entry in self._tools.values():
            if entry.bot_name == bot_name:
                return entry.manifest
        raise KeyError(f"No manifest registered for bot '{bot_name}'")
