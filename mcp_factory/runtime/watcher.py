"""Hot-reload watcher — monitors mcp.yaml manifest files and reloads on change.

Used by hub_server.py when started with --watch.

Events emitted (via callback):
  - "manifest_added"    path: Path  manifest: Manifest
  - "manifest_modified" path: Path  manifest: Manifest   old_name: str | None
  - "manifest_deleted"  path: Path  name: str
  - "no_op"             path: Path  reason: str
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Any

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


WatchEvent = dict[str, Any]
WatchCallback = Callable[[WatchEvent], None]


class ManifestEventHandler(FileSystemEventHandler if _WATCHDOG_AVAILABLE else object):
    """Handles filesystem events for mcp.yaml files."""

    def __init__(self, callback: WatchCallback) -> None:
        if _WATCHDOG_AVAILABLE:
            super().__init__()
        self._callback = callback
        self._lock = threading.Lock()
        self._known: dict[Path, str] = {}  # path -> manifest name

    def on_created(self, event: Any) -> None:
        if not self._is_manifest(event):
            return
        path = Path(event.src_path)
        self._handle_add_or_modify(path, kind="manifest_added")

    def on_modified(self, event: Any) -> None:
        if not self._is_manifest(event):
            return
        path = Path(event.src_path)
        self._handle_add_or_modify(path, kind="manifest_modified")

    def on_deleted(self, event: Any) -> None:
        if not self._is_manifest(event):
            return
        path = Path(event.src_path)
        with self._lock:
            old_name = self._known.pop(path, None)
        if old_name:
            self._callback({"event": "manifest_deleted", "path": path, "name": old_name})
        else:
            self._callback({"event": "no_op", "path": path, "reason": "deleted unknown manifest"})

    def on_moved(self, event: Any) -> None:
        src = Path(event.src_path)
        dest = Path(event.dest_path)
        # Treat src-side as delete, dest-side as add
        if src.name == "mcp.yaml":
            with self._lock:
                old_name = self._known.pop(src, None)
            if old_name:
                self._callback({"event": "manifest_deleted", "path": src, "name": old_name})
        if dest.name == "mcp.yaml":
            self._handle_add_or_modify(dest, kind="manifest_added")

    def _handle_add_or_modify(self, path: Path, *, kind: str) -> None:
        from mcp_factory.manifest import load_manifest
        try:
            manifest = load_manifest(path)
        except Exception as exc:
            self._callback({"event": "no_op", "path": path, "reason": f"load error: {exc}"})
            return

        with self._lock:
            old_name = self._known.get(path)
            self._known[path] = manifest.name

        self._callback({
            "event": kind,
            "path": path,
            "manifest": manifest,
            "old_name": old_name,
        })

    @staticmethod
    def _is_manifest(event: Any) -> bool:
        return (
            not getattr(event, "is_directory", True)
            and Path(getattr(event, "src_path", "")).name == "mcp.yaml"
        )


class ManifestWatcher:
    """Watches one or more root directories for mcp.yaml changes.

    Usage::

        def on_event(ev):
            print(ev)

        watcher = ManifestWatcher([Path("/some/root")], callback=on_event)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(self, roots: list[Path], callback: WatchCallback) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError("watchdog package required: pip install watchdog>=3.0")
        self._roots = roots
        self._callback = callback
        self._handler = ManifestEventHandler(callback)
        self._observer: Any = Observer()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        for root in self._roots:
            if root.exists():
                self._observer.schedule(self._handler, str(root), recursive=True)
        self._observer.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._observer.stop()
        self._observer.join(timeout=5)
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started and self._observer.is_alive()
