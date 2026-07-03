"""MCP Factory — scaffold, scan, and serve MCP servers from mcp.yaml manifests.

This is the canonical CLI implementation, packaged inside ``mcp_factory`` so
that ``pip install jaimenbell-mcp-factory`` ships a working entrypoint
(``mcp-factory-hub`` console script, or ``python -m mcp_factory``).

Modes
-----
Factory (default):  generate a server stub + claude.json entry from one manifest
  mcp-factory-hub --manifest <path> [--output-dir DIR] [--force] [--dry-run] [--verify PATH]

Scan:  batch-discover all projects/*/mcp.yaml and diff against ~/.claude.json
  mcp-factory-hub --scan [ROOT]                  # dry-run diff
  mcp-factory-hub --scan [ROOT] --apply          # write ~/.claude.json (after backup)
  mcp-factory-hub --scan [ROOT] --apply --force  # overwrite existing entries too

Serve:  run as a live MCP hub server (stdio transport, registers with Claude)
  mcp-factory-hub --serve [--scan-root ROOT [--scan-root ROOT2 ...]]

Combining:
  mcp-factory-hub --scan --apply && mcp-factory-hub --serve

For a git checkout without installing the package, ``python hub_server.py``
at the repo root is a thin backward-compat wrapper around this module.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp_factory.manifest import load_manifest
from mcp_factory.generator import generate_server, scaffold_summary
from mcp_factory.config import build_claude_entry, write_config_file, compare_entries
from mcp_factory.scan import (
    compute_scan,
    print_scan_report,
    apply_scan,
    _DEFAULT_SCAN_ROOT,
    _CLAUDE_JSON,
)


_DEFAULT_OUTPUT_CONFIG = Path.home() / ".claude.json.factory-test"
_DEFAULT_OUTPUT_DIR = Path.cwd() / "generated"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="MCP Factory — scaffold, scan, and serve MCP servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mode flags (mutually exclusive)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--serve",
        action="store_true",
        help="Run as a live MCP hub server (stdio transport)",
    )
    mode.add_argument(
        "--register",
        action="store_true",
        help="Compose: scan --apply then serve (shorthand for --scan --apply && --serve)",
    )
    mode.add_argument(
        "--scan",
        nargs="?",
        const=str(_DEFAULT_SCAN_ROOT),
        metavar="ROOT",
        help=(
            f"Scan ROOT for mcp.yaml manifests and diff against ~/.claude.json "
            f"(default root: {_DEFAULT_SCAN_ROOT})"
        ),
    )
    # --manifest requires neither --serve nor --scan
    p.add_argument(
        "--manifest",
        metavar="PATH",
        help="Path to mcp.yaml manifest (factory mode)",
    )

    # Scan / serve shared
    p.add_argument(
        "--scan-root",
        action="append",
        dest="scan_roots",
        metavar="ROOT",
        help=(
            f"Root directory for manifest discovery (repeatable, "
            f"default: {_DEFAULT_SCAN_ROOT})"
        ),
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="(--scan) Actually write ~/.claude.json after backing it up",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="(--scan) Update already-registered entries. (factory) Overwrite generated stubs.",
    )
    p.add_argument(
        "--scan-target",
        default=None,
        metavar="PATH",
        help=(
            "(--scan) Target claude.json to diff/write. "
            f"Default: ~/.claude.json. Override in tests to avoid touching the live config."
        ),
    )

    # Factory-mode options
    p.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        metavar="DIR",
        help=f"Where to write generated stubs (default: {_DEFAULT_OUTPUT_DIR})",
    )
    p.add_argument(
        "--output-config",
        default=str(_DEFAULT_OUTPUT_CONFIG),
        metavar="PATH",
        help=f"Where to write the claude.json entry (default: {_DEFAULT_OUTPUT_CONFIG})",
    )
    p.add_argument(
        "--watch",
        action="store_true",
        help="(--serve) Hot-reload manifests on change (requires watchdog pkg)",
    )
    p.add_argument("--dry-run", action="store_true", help="Preview only, no files written")
    p.add_argument(
        "--verify",
        metavar="LIVE_PATH",
        help="Path to live claude.json to compare against (self-verification)",
    )

    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Printers
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _print_ok(msg: str) -> None:
    print(f"  [OK]  {msg}")


def _print_warn(msg: str) -> None:
    print(f"  [!!]  {msg}", file=sys.stderr)


def _print_err(msg: str) -> None:
    print(f"  [ERR] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Serve mode
# ---------------------------------------------------------------------------

def _run_serve(args: argparse.Namespace) -> int:
    from mcp_factory.scan import scan_manifests

    roots = [Path(r) for r in (args.scan_roots or [str(_DEFAULT_SCAN_ROOT)])]
    manifests = []
    seen_names: set[str] = set()

    for root in roots:
        print(f"[hub] Scanning manifests under {root} ...", file=sys.stderr)
        for path, manifest, err in scan_manifests(root):
            if err:
                print(f"[hub] skip {path}: {err}", file=sys.stderr)
            elif manifest:
                if manifest.name in seen_names:
                    print(
                        f"[hub] skip {path}: duplicate name '{manifest.name}'",
                        file=sys.stderr,
                    )
                else:
                    seen_names.add(manifest.name)
                    manifests.append(manifest)

    if not manifests:
        print("[hub] No manifests found — hub will serve only meta-tools.", file=sys.stderr)

    from mcp_factory.runtime.hub import run_hub
    asyncio.run(run_hub(manifests))
    return 0


# ---------------------------------------------------------------------------
# Scan mode
# ---------------------------------------------------------------------------

def _run_scan(args: argparse.Namespace) -> int:
    roots = args.scan_roots or []
    root = Path(args.scan if args.scan else (roots[0] if roots else str(_DEFAULT_SCAN_ROOT)))
    apply: bool = args.apply
    force: bool = args.force
    target = Path(args.scan_target) if args.scan_target else _CLAUDE_JSON

    result = compute_scan(root, claude_json_path=target, force=force)
    print_scan_report(result, apply=apply)

    if apply:
        if not result.additions and not result.updates:
            print("Nothing to write.")
            return 0
        backup = apply_scan(result, claude_json_path=target)
        print(f"Backup written to: {backup}")
        print(f"Updated:           {target}")

    return 0


# ---------------------------------------------------------------------------
# Factory mode (original Day 1 behaviour)
# ---------------------------------------------------------------------------

def _run_factory(args: argparse.Namespace) -> int:
    if not args.manifest:
        _print_err("--manifest is required in factory mode (or use --serve / --scan)")
        return 1

    output_dir = Path(args.output_dir)
    output_config = Path(args.output_config)
    dry_run: bool = args.dry_run

    _print_header("MCP Factory")
    print(f"  Manifest:      {args.manifest}")
    print(f"  Output dir:    {output_dir}")
    print(f"  Output config: {output_config}")
    if dry_run:
        print("  Mode:          DRY RUN (no files written)")

    # 1. Load manifest
    print()
    print("Step 1: Loading manifest...")
    try:
        manifest = load_manifest(args.manifest)
    except (FileNotFoundError, ValueError) as e:
        _print_err(f"Manifest error: {e}")
        return 1
    _print_ok(f"Loaded '{manifest.name}' — {len(manifest.tools)} tools: {manifest.tool_names}")

    # 2. Generate scaffold
    print()
    print("Step 2: Scaffold generation...")
    summary = scaffold_summary(manifest, output_dir)
    generated_script: Path | None = None

    if summary["action"] == "reference_existing":
        _print_ok(f"Referencing existing script: {summary['script']}")
    else:
        try:
            generated_script = generate_server(
                manifest, output_dir, force=args.force, dry_run=dry_run
            )
            if generated_script:
                _print_ok(f"Generated stub: {generated_script}")
        except FileExistsError as e:
            _print_err(str(e))
            return 1
        except NotImplementedError as e:
            _print_err(str(e))
            return 1

    # 3. Build claude.json entry
    print()
    print("Step 3: Building claude.json entry...")
    try:
        entry = build_claude_entry(manifest, generated_script=generated_script)
    except ValueError as e:
        _print_err(str(e))
        return 1

    # 4. Write config
    print()
    print("Step 4: Writing config...")
    try:
        write_config_file(
            [manifest],
            output_config,
            generated_scripts={manifest.name: generated_script} if generated_script else {},
            dry_run=dry_run,
        )
    except OSError as e:
        _print_err(f"Failed to write config: {e}")
        return 1

    if not dry_run:
        _print_ok(f"Config written: {output_config}")
        print()
        print("  Entry preview:")
        print(f"    {json.dumps({manifest.name: entry}, indent=4)}")

    # 5. Self-verification (optional)
    if args.verify:
        print()
        print("Step 5: Self-verification...")
        live_path = Path(args.verify)
        try:
            live_data = json.loads(live_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _print_err(f"Cannot read live config '{live_path}': {e}")
            return 1

        live_servers = live_data.get("mcpServers", {})
        live_entry = live_servers.get(manifest.name)
        if live_entry is None:
            _print_warn(
                f"Server '{manifest.name}' not found in live config at {live_path}. "
                f"Known servers: {list(live_servers.keys())}"
            )
        else:
            matches, diffs = compare_entries(entry, live_entry)
            if matches:
                _print_ok(f"MATCH — factory entry is identical to live '{manifest.name}' entry.")
            else:
                _print_warn(f"MISMATCH — {len(diffs)} difference(s):")
                for d in diffs:
                    print(f"           {d}", file=sys.stderr)
                return 2

    print()
    print("Done.")
    if not dry_run:
        print(f"  Config written to: {output_config}")
        print(f"  To register in Claude, add the entry from {output_config}")
        print(f"  to ~/.claude.json under 'mcpServers'.")
    return 0


# ---------------------------------------------------------------------------
# Register mode (compose: scan --apply, then serve)
# ---------------------------------------------------------------------------

def _run_register(args: argparse.Namespace) -> int:
    """Scan, apply to claude.json, then start the hub server."""
    roots = args.scan_roots or [str(_DEFAULT_SCAN_ROOT)]
    target = Path(args.scan_target) if args.scan_target else _CLAUDE_JSON
    force: bool = args.force

    print(f"[register] Scanning {roots} ...", file=sys.stderr)
    all_result = None
    for root_str in roots:
        root = Path(root_str)
        from mcp_factory.scan import compute_scan, print_scan_report, apply_scan
        result = compute_scan(root, claude_json_path=target, force=force)
        print_scan_report(result, apply=True)
        if result.additions or result.updates:
            backup = apply_scan(result, claude_json_path=target)
            print(f"[register] Backup -> {backup}", file=sys.stderr)
        all_result = result

    print("[register] Scan+apply complete — starting hub ...", file=sys.stderr)
    return _run_serve(args)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.serve:
        return _run_serve(args)
    if args.register:
        return _run_register(args)
    if args.scan is not None:
        return _run_scan(args)
    return _run_factory(args)


if __name__ == "__main__":
    sys.exit(run())
