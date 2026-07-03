#!/usr/bin/env python3
"""Backward-compat wrapper -- CLI logic now lives in mcp_factory/cli.py
(packaged, so `pip install jaimenbell-mcp-factory` ships a working entrypoint).
Kept for dev-checkout convenience: `python hub_server.py ...` still works.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from any working directory in a dev checkout.
sys.path.insert(0, str(Path(__file__).parent))

from mcp_factory.cli import (  # noqa: E402,F401 -- re-exported for dev-checkout compat
    run,
    _parse_args,
    _run_serve,
    _run_scan,
    _run_factory,
    _run_register,
    _print_header,
    _print_ok,
    _print_warn,
    _print_err,
    _DEFAULT_OUTPUT_CONFIG,
    _DEFAULT_OUTPUT_DIR,
)

if __name__ == "__main__":
    sys.exit(run())
