"""Scan mode — batch-discover mcp.yaml manifests and register them into ~/.claude.json.

Usage (via hub_server.py):
    python hub_server.py --scan [ROOT]              # dry-run diff
    python hub_server.py --scan [ROOT] --apply      # write ~/.claude.json (after backup)
    python hub_server.py --scan [ROOT] --apply --force  # overwrite existing entries too
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp_factory.manifest import Manifest, load_manifest
from mcp_factory.config import build_claude_entry, compare_entries


_DEFAULT_SCAN_ROOT = Path(
    os.environ.get("MCP_FACTORY_SCAN_ROOT", str(Path.home() / "projects"))
)
_CLAUDE_JSON = Path.home() / ".claude.json"


@dataclass
class ScanResult:
    root: Path
    manifests: list[Manifest] = field(default_factory=list)
    additions: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    updates: list[tuple[str, dict[str, Any], list[str]]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    load_errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def total_found(self) -> int:
        return len(self.manifests) + len(self.load_errors)

    @property
    def total_registered(self) -> int:
        return len(self.additions) + len(self.updates)


def scan_manifests(root: Path) -> list[tuple[Path, Manifest | None, str | None]]:
    """Find all mcp.yaml files one level deep under root.

    Returns list of (path, manifest_or_None, error_or_None).
    """
    results = []
    for yaml_path in sorted(root.glob("*/mcp.yaml")):
        try:
            m = load_manifest(yaml_path)
            results.append((yaml_path, m, None))
        except Exception as exc:
            results.append((yaml_path, None, str(exc)))
    return results


def compute_scan(
    root: Path,
    *,
    claude_json_path: Path = _CLAUDE_JSON,
    force: bool = False,
) -> ScanResult:
    """Compute what would change in claude.json without writing anything."""
    result = ScanResult(root=root)

    # Load existing claude.json
    existing: dict[str, Any] = {}
    if claude_json_path.exists():
        try:
            data = json.loads(claude_json_path.read_text(encoding="utf-8"))
            existing = data.get("mcpServers", {})
        except Exception:
            pass  # treat as empty

    raw = scan_manifests(root)
    for path, manifest, err in raw:
        if err:
            result.load_errors.append((path, err))
            continue

        assert manifest is not None
        result.manifests.append(manifest)

        # Try to build the entry
        try:
            entry = build_claude_entry(manifest)
        except ValueError as exc:
            result.skipped.append((manifest.name, f"cannot build entry: {exc}"))
            continue

        name = manifest.name
        if name in existing:
            if not force:
                result.skipped.append((name, "already registered (use --force to update)"))
                continue
            matches, diffs = compare_entries(entry, existing[name])
            if matches:
                result.skipped.append((name, "already registered, no changes"))
            else:
                result.updates.append((name, entry, diffs))
        else:
            result.additions.append((name, entry))

    return result


def print_scan_report(result: ScanResult, *, apply: bool = False) -> None:
    """Print a human-readable diff report."""
    print(f"\nScan root:  {result.root}")
    print(f"Found:      {result.total_found} manifest(s)  ({len(result.load_errors)} load error(s))")

    if result.load_errors:
        print("\nLoad errors:")
        for path, err in result.load_errors:
            print(f"  [ERR] {path}: {err}")

    print(f"\nAdditions ({len(result.additions)}):")
    if result.additions:
        for name, entry in result.additions:
            print(f"  + {name}")
            print(f"      {entry['command']} {entry.get('args', [])}")
    else:
        print("  (none)")

    if result.updates:
        print(f"\nUpdates ({len(result.updates)}):")
        for name, entry, diffs in result.updates:
            print(f"  ~ {name}")
            for d in diffs:
                print(f"      {d}")

    if result.skipped:
        print(f"\nSkipped ({len(result.skipped)}):")
        for name, reason in result.skipped:
            print(f"  - {name}: {reason}")

    if not apply:
        print("\n(Dry run — use --apply to write changes)")
    else:
        print(f"\nRegistered {result.total_registered} server(s).")


def apply_scan(
    result: ScanResult,
    *,
    claude_json_path: Path = _CLAUDE_JSON,
) -> Path:
    """Merge additions+updates into claude.json after backing it up.

    Returns the backup path.
    """
    # Load current content (or start fresh)
    if claude_json_path.exists():
        current: dict[str, Any] = json.loads(claude_json_path.read_text(encoding="utf-8"))
    else:
        current = {}

    servers: dict[str, Any] = dict(current.get("mcpServers", {}))

    for name, entry in result.additions:
        servers[name] = entry
    for name, entry, _ in result.updates:
        servers[name] = entry

    current["mcpServers"] = servers

    # Backup
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = claude_json_path.parent / (claude_json_path.name + f".scan-backup-{timestamp}")
    if claude_json_path.exists():
        shutil.copy2(claude_json_path, backup_path)

    claude_json_path.parent.mkdir(parents=True, exist_ok=True)
    claude_json_path.write_text(json.dumps(current, indent=2), encoding="utf-8")

    return backup_path
