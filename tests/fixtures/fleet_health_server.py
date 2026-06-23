#!/usr/bin/env python3
"""Minimal stub MCP server used as a fixture target.

This file exists only so that `fleet_health.yaml`'s `runtime.script` resolves
to a real path during tests (exercising the "reference an existing script"
code path). It is not a working server.
"""
from __future__ import annotations


def main() -> None:  # pragma: no cover - fixture stub, never executed by tests
    raise SystemExit("fixture stub: not a runnable server")


if __name__ == "__main__":  # pragma: no cover
    main()
