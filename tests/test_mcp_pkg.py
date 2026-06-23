"""Deliverable 1: verify mcp package is importable under Python 3.14."""
import importlib.metadata
import importlib


def test_mcp_importable():
    mod = importlib.import_module("mcp")
    assert mod is not None


def test_mcp_version_semver():
    ver = importlib.metadata.version("mcp")
    parts = ver.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts[:2])


def test_mcp_version_at_least_1():
    ver = importlib.metadata.version("mcp")
    major = int(ver.split(".")[0])
    assert major >= 1
