"""Bundled demo manifest -- mcp-factory's own directory-listing fallback.

See mcp_factory/cli.py::_run_serve for exactly when this activates: only
when a `--serve` scan finds zero real manifests AND no explicit scan-root
override was given (neither --scan-root nor MCP_FACTORY_SCAN_ROOT), and
only if not disabled via --no-demo / MCP_FACTORY_NO_DEMO=1.

HONESTY CONSTRAINT: this bot's name, description, and every tool description
carry an unmistakable "[DEMO]" / "NOT A REAL INTEGRATION" marker so it can
never be confused for a real tool in a listing, a tool count, or a
directory score. Activation always prints a loud stderr line naming what
happened -- see cli.py. Shipping a demo manifest to improve a directory
score is defensible only as long as that stays true.
"""
from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path

from mcp_factory.manifest import Manifest, load_manifest

DEMO_BOT_NAME = "mcp-factory-demo"


def load_demo_manifest() -> Manifest | None:
    """Load the bundled demo manifest.

    Returns None (never raises) if the packaged demo files are missing --
    e.g. a broken/partial install -- so a corrupt install degrades to
    "no manifests found" rather than crashing the hub.
    """
    try:
        demo_pkg = importlib.resources.files("mcp_factory.demo")
        manifest_path = Path(str(demo_pkg / "mcp.yaml"))
        manifest = load_manifest(manifest_path)
    except Exception as exc:  # pragma: no cover - defensive, broken-install only
        print(
            f"[hub] WARNING: could not load bundled demo manifest: {exc}",
            file=sys.stderr,
        )
        return None

    # The yaml's runtime.command is a placeholder ("python"); pin it to the
    # interpreter actually running this process so it works regardless of
    # what "python" resolves to (or whether it's on PATH at all).
    manifest.runtime.command = sys.executable
    return manifest
