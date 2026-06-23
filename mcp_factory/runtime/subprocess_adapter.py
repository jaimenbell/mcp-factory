"""SubprocessAdapter — spawns an MCP server as a subprocess and proxies JSON-RPC calls.

Wire format: newline-delimited JSON-RPC 2.0 over stdin/stdout.
Lifecycle: lazy-start on first call, keep-alive per session, stop() for cleanup.
Thread-safe: a Lock serialises concurrent callers so stdin/stdout don't interleave.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mcp_factory.manifest import Manifest


class SubprocessError(RuntimeError):
    """Raised when the subprocess MCP server returns an error or dies."""


class SubprocessAdapter:
    """Manages a single subprocess MCP server for one manifest."""

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._initialized = False
        self._last_used: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the subprocess, starting it lazily if needed.

        Returns the JSON-RPC result dict (has a 'content' key).
        Thread-safe: concurrent callers are serialised.
        """
        with self._lock:
            if not self._initialized:
                self._start()
            self._last_used = time.monotonic()
            return self._do_call_tool(tool_name, arguments)

    def stop(self) -> None:
        """Gracefully shut down the subprocess, killing if it doesn't exit in 5 s."""
        with self._lock:
            self._stop_unlocked()

    @property
    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def idle_seconds(self) -> float:
        """Seconds since last call_tool(); 0.0 if never called."""
        if self._last_used == 0.0:
            return 0.0
        return time.monotonic() - self._last_used

    # ------------------------------------------------------------------
    # Internal — must only be called while holding self._lock
    # ------------------------------------------------------------------

    def _start(self) -> None:
        script = self._resolve_script()
        cmd = [self.manifest.runtime.command, str(script)]

        import os
        env = {**os.environ, **self.manifest.env}

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._do_initialize()
        self._initialized = True

    def _resolve_script(self) -> Path:
        rt = self.manifest.runtime
        if rt.has_existing_script and rt.script_path:
            return rt.script_path
        if rt.output_path:
            return rt.output_path
        raise SubprocessError(
            f"Manifest '{self.manifest.name}': no script to run. "
            "Set runtime.script (existing) or runtime.output."
        )

    def _do_initialize(self) -> None:
        req_id = self._alloc_id()
        self._write({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-factory-hub", "version": "0.1.0"},
            },
        })
        resp = self._read_response(req_id)
        if "error" in resp:
            raise SubprocessError(f"initialize error: {resp['error']}")
        # Send initialized notification (no response expected)
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _do_call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        req_id = self._alloc_id()
        self._write({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        resp = self._read_response(req_id)
        if "error" in resp:
            raise SubprocessError(f"tools/call '{tool_name}' error: {resp['error']}")
        return resp.get("result", {})

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _write(self, msg: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise SubprocessError("Subprocess not running")
        line = json.dumps(msg) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        self._proc.stdin.flush()

    def _read_message(self) -> dict[str, Any]:
        if not self._proc or not self._proc.stdout:
            raise SubprocessError("Subprocess not running")
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                # Check stderr for a clue
                stderr_hint = ""
                if self._proc.stderr:
                    try:
                        stderr_hint = self._proc.stderr.read(512).decode("utf-8", errors="replace")
                    except Exception:
                        pass
                raise SubprocessError(
                    f"Subprocess '{self.manifest.name}' closed stdout. stderr: {stderr_hint!r}"
                )
            line = raw.decode("utf-8").strip()
            if line:
                return json.loads(line)

    def _read_response(self, expected_id: int) -> dict[str, Any]:
        """Read messages until we get a response matching expected_id."""
        while True:
            msg = self._read_message()
            if "id" not in msg:
                # Notification — skip
                continue
            if msg["id"] == expected_id:
                return msg
            # Unexpected id — log to stderr and keep reading
            print(
                f"[hub] unexpected response id {msg['id']} (expected {expected_id}), skipping",
                file=sys.stderr,
            )

    def _stop_unlocked(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except OSError:
            pass
        self._proc = None
        self._initialized = False
