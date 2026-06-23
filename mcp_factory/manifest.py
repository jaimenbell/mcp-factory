"""Manifest — loads and validates mcp.yaml files.

Schema:
    name: str                    # unique MCP server name (used as key in claude.json)
    description: str             # semantic description for Claude routing
    runtime:
        type: python | node | binary
        command: str             # full path to interpreter / executable
        script: str              # path to existing MCP server script
        output: str              # (optional) where to write a generated scaffold
    tools:
        - name: str
          description: str
          args:
              - name: str
                type: string | number | boolean | object | array
                required: bool   # default true
                description: str
    env_required: [str, ...]     # env var names required at runtime
    env: {KEY: VALUE}            # static env vars written to claude.json
    tags: [str, ...]
    priority: high | medium | low
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


VALID_RUNTIME_TYPES = {"python", "node", "binary"}
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_ARG_TYPES = {"string", "number", "boolean", "object", "array"}


@dataclass
class ArgSpec:
    name: str
    type: str = "string"
    required: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ArgSpec":
        if "name" not in d:
            raise ValueError("arg missing required field 'name'")
        arg_type = d.get("type", "string")
        if arg_type not in VALID_ARG_TYPES:
            raise ValueError(f"arg '{d['name']}': unknown type '{arg_type}'. Valid: {VALID_ARG_TYPES}")
        return cls(
            name=d["name"],
            type=arg_type,
            required=bool(d.get("required", True)),
            description=str(d.get("description", "")),
        )

    def to_json_schema_property(self) -> dict[str, Any]:
        prop: dict[str, Any] = {"type": self.type}
        if self.description:
            prop["description"] = self.description
        return prop


@dataclass
class ToolSpec:
    name: str
    description: str
    args: list[ArgSpec] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolSpec":
        if "name" not in d:
            raise ValueError("tool missing required field 'name'")
        if "description" not in d:
            raise ValueError(f"tool '{d['name']}' missing required field 'description'")
        args = [ArgSpec.from_dict(a) for a in d.get("args", [])]
        return cls(name=d["name"], description=str(d["description"]), args=args)

    def to_input_schema(self) -> dict[str, Any]:
        props = {a.name: a.to_json_schema_property() for a in self.args}
        required = [a.name for a in self.args if a.required]
        return {"type": "object", "properties": props, "required": required}


@dataclass
class RuntimeSpec:
    type: str
    command: str
    script: str = ""
    output: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuntimeSpec":
        rt = d.get("type", "python")
        if rt not in VALID_RUNTIME_TYPES:
            raise ValueError(f"runtime.type '{rt}' invalid. Valid: {VALID_RUNTIME_TYPES}")
        if "command" not in d:
            raise ValueError("runtime missing required field 'command'")
        return cls(
            type=rt,
            command=str(d["command"]),
            script=str(d.get("script", "")),
            output=str(d.get("output", "")),
        )

    @property
    def script_path(self) -> Path | None:
        return Path(self.script) if self.script else None

    @property
    def output_path(self) -> Path | None:
        return Path(self.output) if self.output else None

    @property
    def has_existing_script(self) -> bool:
        return bool(self.script) and (Path(self.script).exists() if self.script else False)


@dataclass
class Manifest:
    name: str
    description: str
    runtime: RuntimeSpec
    tools: list[ToolSpec]
    env_required: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    priority: str = "medium"
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any], source_path: Path | None = None) -> "Manifest":
        for req in ("name", "description", "runtime", "tools"):
            if req not in d:
                raise ValueError(f"manifest missing required field '{req}'")

        priority = d.get("priority", "medium")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority '{priority}' invalid. Valid: {VALID_PRIORITIES}")

        return cls(
            name=str(d["name"]),
            description=str(d["description"]),
            runtime=RuntimeSpec.from_dict(d["runtime"]),
            tools=[ToolSpec.from_dict(t) for t in d["tools"]],
            env_required=[str(e) for e in d.get("env_required", [])],
            env={str(k): str(v) for k, v in d.get("env", {}).items()},
            tags=[str(t) for t in d.get("tags", [])],
            priority=priority,
            source_path=source_path,
        )

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]


def load_manifest(path: str | Path) -> Manifest:
    """Load and validate an mcp.yaml manifest file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"manifest not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"manifest must be a YAML mapping, got {type(raw).__name__}")
    manifest = Manifest.from_dict(raw, source_path=p)
    # Resolve a relative runtime.script against the manifest's own directory so a
    # manifest can portably reference a server file that sits next to it.
    # Absolute paths are left untouched.
    if manifest.runtime.script and not Path(manifest.runtime.script).is_absolute():
        manifest.runtime.script = str((p.parent / manifest.runtime.script).resolve())
    return manifest
