#!/usr/bin/env python3
"""Register mcp-factory-hub in ~/.claude.json.

Backs up first; never overwrites existing entries.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

_CLAUDE_JSON = Path.home() / ".claude.json"
_BACKUP_SUFFIX = ".day3-prereg-backup"
_HUB_SERVER = Path(__file__).parent.parent / "hub_server.py"
# Populate via MCP_FACTORY_SCAN_ROOTS (os.pathsep-delimited list of directories).
# Example (Windows):  set MCP_FACTORY_SCAN_ROOTS=C:\Users\you\projects;C:\Users\you\Claude
# Example (Unix):     export MCP_FACTORY_SCAN_ROOTS=/home/you/projects:/home/you/Claude
_SCAN_ROOTS = [r for r in os.environ.get("MCP_FACTORY_SCAN_ROOTS", "").split(os.pathsep) if r]


def register(*, dry_run: bool = False) -> int:
    hub_py = str(_HUB_SERVER.resolve())

    data = json.loads(_CLAUDE_JSON.read_text(encoding="utf-8"))
    servers: dict = data.setdefault("mcpServers", {})

    if "mcp-factory-hub" in servers:
        print("[register] 'mcp-factory-hub' already in mcpServers — nothing to do.")
        return 0

    entry = {
        "command": "python",
        "args": [hub_py, "--serve"]
        + [arg for root in _SCAN_ROOTS for arg in ("--scan-root", root)],
        "env": {},
    }

    if dry_run:
        print("[register] DRY RUN — would add:")
        print(json.dumps({"mcp-factory-hub": entry}, indent=2))
        return 0

    # Backup
    backup = _CLAUDE_JSON.parent / (_CLAUDE_JSON.name + _BACKUP_SUFFIX)
    shutil.copy2(_CLAUDE_JSON, backup)
    print(f"[register] Backup -> {backup}")

    servers["mcp-factory-hub"] = entry
    _CLAUDE_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[register] Added 'mcp-factory-hub' to {_CLAUDE_JSON}")
    return 0


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    sys.exit(register(dry_run=dry))
