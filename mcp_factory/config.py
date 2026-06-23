"""Config writer — generates ~/.claude.json mcpServers entries from manifests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp_factory.manifest import Manifest


def build_claude_entry(manifest: Manifest, generated_script: Path | None = None) -> dict[str, Any]:
    """Build the claude.json mcpServers entry for a manifest.

    If the manifest references an existing script (runtime.script), use that path.
    If the factory generated a new script (generated_script arg), use that path.
    """
    if manifest.runtime.has_existing_script:
        script_path = manifest.runtime.script
    elif generated_script:
        script_path = str(generated_script)
    elif manifest.runtime.output_path:
        script_path = str(manifest.runtime.output_path)
    else:
        raise ValueError(
            f"Cannot build claude.json entry for '{manifest.name}': "
            "no existing script and no generated output path. "
            "Set runtime.script (existing) or runtime.output in the manifest."
        )

    if manifest.runtime.type == "python":
        entry: dict[str, Any] = {
            "command": manifest.runtime.command,
            "args": [script_path],
            "env": dict(manifest.env),
        }
    elif manifest.runtime.type == "node":
        entry = {
            "command": manifest.runtime.command,
            "args": [script_path],
            "env": dict(manifest.env),
        }
    elif manifest.runtime.type == "binary":
        entry = {
            "command": manifest.runtime.command,
            "args": [] if not script_path else [script_path],
            "env": dict(manifest.env),
        }
    else:
        raise ValueError(f"Unknown runtime type '{manifest.runtime.type}'")

    return entry


def write_config_file(
    manifests: list[Manifest],
    output_path: Path | str,
    *,
    generated_scripts: dict[str, Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write a claude.json-format config file with one entry per manifest.

    Returns the full mcpServers dict that was (or would be) written.
    """
    output_path = Path(output_path)
    generated_scripts = generated_scripts or {}

    mcp_servers: dict[str, Any] = {}
    for m in manifests:
        gen = generated_scripts.get(m.name)
        mcp_servers[m.name] = build_claude_entry(m, generated_script=gen)

    payload = {"mcpServers": mcp_servers}

    if dry_run:
        print(f"[dry-run] Would write {output_path}:")
        print(json.dumps(payload, indent=2))
        return mcp_servers

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return mcp_servers


def compare_entries(factory_entry: dict, live_entry: dict) -> tuple[bool, list[str]]:
    """Compare a factory-generated entry to a live claude.json entry.

    Returns (matches: bool, diffs: list[str]).
    """
    diffs = []

    def norm_path(p: str) -> str:
        return p.replace("\\\\", "\\").replace("/", "\\")

    f_cmd = norm_path(factory_entry.get("command", ""))
    l_cmd = norm_path(live_entry.get("command", ""))
    if f_cmd != l_cmd:
        diffs.append(f"command: factory={f_cmd!r} live={l_cmd!r}")

    f_args = [norm_path(a) for a in factory_entry.get("args", [])]
    l_args = [norm_path(a) for a in live_entry.get("args", [])]
    if f_args != l_args:
        diffs.append(f"args: factory={f_args} live={l_args}")

    f_env = factory_entry.get("env", {})
    l_env = live_entry.get("env", {})
    if f_env != l_env:
        diffs.append(f"env: factory={f_env} live={l_env}")

    return len(diffs) == 0, diffs
