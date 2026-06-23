"""Deliverable 5: hot-reload watcher tests.

Tests cover: add, modify, delete, and no-op (non-manifest file change).
Uses watchdog's Observer directly + fake filesystem events to avoid
requiring a live filesystem watcher in CI.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_factory.runtime.watcher import ManifestEventHandler, ManifestWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_event(src: str, is_dir: bool = False, dest: str | None = None):
    ev = MagicMock()
    ev.src_path = src
    ev.is_directory = is_dir
    if dest is not None:
        ev.dest_path = dest
    return ev


_MINIMAL_YAML = """\
name: test-bot
description: "Watcher test bot"
runtime:
  type: python
  command: python
  output: /tmp/test_server.py
tools:
  - name: ping
    description: "Ping"
    args: []
"""


# ---------------------------------------------------------------------------
# Unit tests using fake events
# ---------------------------------------------------------------------------

class TestManifestEventHandler:
    def test_add_emits_manifest_added(self, tmp_path):
        yaml = tmp_path / "test-bot" / "mcp.yaml"
        yaml.parent.mkdir()
        yaml.write_text(_MINIMAL_YAML)

        events = []
        handler = ManifestEventHandler(events.append)
        handler.on_created(_fake_event(str(yaml)))

        assert len(events) == 1
        assert events[0]["event"] == "manifest_added"
        assert events[0]["manifest"].name == "test-bot"

    def test_modify_emits_manifest_modified(self, tmp_path):
        yaml = tmp_path / "test-bot" / "mcp.yaml"
        yaml.parent.mkdir()
        yaml.write_text(_MINIMAL_YAML)

        events = []
        handler = ManifestEventHandler(events.append)
        handler.on_created(_fake_event(str(yaml)))   # seed known
        handler.on_modified(_fake_event(str(yaml)))

        modified = [e for e in events if e["event"] == "manifest_modified"]
        assert len(modified) == 1
        assert modified[0]["manifest"].name == "test-bot"

    def test_delete_emits_manifest_deleted(self, tmp_path):
        yaml = tmp_path / "test-bot" / "mcp.yaml"
        yaml.parent.mkdir()
        yaml.write_text(_MINIMAL_YAML)

        events = []
        handler = ManifestEventHandler(events.append)
        handler.on_created(_fake_event(str(yaml)))   # seed known
        yaml.unlink()
        handler.on_deleted(_fake_event(str(yaml)))

        deleted = [e for e in events if e["event"] == "manifest_deleted"]
        assert len(deleted) == 1
        assert deleted[0]["name"] == "test-bot"
        assert deleted[0]["path"] == yaml

    def test_non_manifest_file_emits_no_op(self, tmp_path):
        """Changes to non-mcp.yaml files must not emit events."""
        txt = tmp_path / "README.txt"
        txt.write_text("hello")

        events = []
        handler = ManifestEventHandler(events.append)
        handler.on_created(_fake_event(str(txt)))
        handler.on_modified(_fake_event(str(txt)))

        assert events == [], f"Expected no events for non-manifest file, got {events}"

    def test_load_error_emits_no_op(self, tmp_path):
        """Bad YAML emits a no_op event, not an exception."""
        yaml = tmp_path / "bad" / "mcp.yaml"
        yaml.parent.mkdir()
        yaml.write_text("not: valid: yaml: [[[")

        events = []
        handler = ManifestEventHandler(events.append)
        handler.on_created(_fake_event(str(yaml)))

        assert len(events) == 1
        assert events[0]["event"] == "no_op"
        assert "load error" in events[0]["reason"]

    def test_delete_unknown_emits_no_op(self, tmp_path):
        """Deleting a file not previously added emits no_op."""
        yaml = tmp_path / "ghost" / "mcp.yaml"
        yaml.parent.mkdir()
        yaml.write_text(_MINIMAL_YAML)

        events = []
        handler = ManifestEventHandler(events.append)
        yaml.unlink()
        handler.on_deleted(_fake_event(str(yaml)))

        assert events[0]["event"] == "no_op"
        assert "unknown" in events[0]["reason"]


# ---------------------------------------------------------------------------
# Integration: live filesystem watcher
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("watchdog"),
    reason="watchdog not installed",
)
class TestManifestWatcherLive:
    def test_watcher_starts_and_stops(self, tmp_path):
        watcher = ManifestWatcher([tmp_path], callback=lambda e: None)
        watcher.start()
        assert watcher.is_running
        watcher.stop()
        assert not watcher.is_running

    def test_watcher_detects_new_manifest(self, tmp_path):
        events = []
        watcher = ManifestWatcher([tmp_path], callback=events.append)
        watcher.start()
        try:
            bot_dir = tmp_path / "live-bot"
            bot_dir.mkdir()
            time.sleep(0.1)
            (bot_dir / "mcp.yaml").write_text(_MINIMAL_YAML)
            # Give the observer thread time to fire
            deadline = time.time() + 3
            while time.time() < deadline:
                if any(e.get("event") == "manifest_added" for e in events):
                    break
                time.sleep(0.05)
        finally:
            watcher.stop()

        added = [e for e in events if e.get("event") == "manifest_added"]
        assert added, f"Expected manifest_added event, got {events}"
