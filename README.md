---
title: MCP Factory
type: project-readme
tags: [mcp, factory, claude]
---

# MCP Factory

[![CI](https://github.com/jaimenbell/MCP-Factory/actions/workflows/ci.yml/badge.svg)](https://github.com/jaimenbell/MCP-Factory/actions/workflows/ci.yml) ![tests](https://img.shields.io/badge/tests-224%20passing-brightgreen) ![python](https://img.shields.io/badge/python-%E2%89%A53.12-blue) [![PyPI](https://img.shields.io/pypi/v/jaimenbell-mcp-factory)](https://pypi.org/project/jaimenbell-mcp-factory/) [![MCP Registry](https://img.shields.io/badge/MCP%20Registry-io.github.jaimenbell%2Fmcp--factory-blueviolet)](https://registry.modelcontextprotocol.io/)

> *The test count is verifiable below (`python -m pytest tests/` → **224 passed, 8 skipped**) and enforced in CI by `scripts/check_readme_counts.py`, which fails the build if this README's counts drift from the live suite.*

## 60-Second Quickstart

**From PyPI (registry users):**

```bash
pip install jaimenbell-mcp-factory
mcp-factory-hub --serve
# equivalent: python -m mcp_factory --serve
```

**From a git checkout (contributors):** see the `python hub_server.py ...` examples throughout this README — `hub_server.py` at the repo root is a backward-compat wrapper around the same `mcp_factory.cli` module the console script runs, so behavior is identical either way.

**The manifest-driven engine behind the MCP Integration Sprint.** Write one `mcp.yaml` for a bot repo and the factory generates the server stub and the `~/.claude.json` entry; run the hub and it serves every bot's tools through a single MCP endpoint.

The SDK wrapper is the easy part. What makes an MCP server safe to put in front of a real internal tool — **scoped auth/env, fail-soft error handling, validated manifests, a collision-safe registry, and a real test suite** — is the engineering this engine is built around. That same production layer is hand-built per engagement; the factory scaffolds it, it doesn't fake it.

### Browse before you reply

This repo is public **so you can verify the discipline instead of taking my word for it.** Every claim below maps to a file you can open:

| Claim | Where it lives | What to look for |
|---|---|---|
| **Validated, env-scoped manifests** | [`mcp_factory/manifest.py`](mcp_factory/manifest.py) | strict `from_dict` validation (raises on missing/invalid fields); the `env_required` / `env` model that scopes which secrets a server may see |
| **Fail-soft subprocess proxying** | [`mcp_factory/runtime/subprocess_adapter.py`](mcp_factory/runtime/subprocess_adapter.py) | typed `SubprocessError`, lazy start, JSON-RPC error surfacing, `timeout`/`OSError`-guarded teardown + `atexit` cleanup — a dead bot returns a clean error, it doesn't crash the hub |
| **Collision-safe, manifest-driven registry** | [`mcp_factory/runtime/registry.py`](mcp_factory/runtime/registry.py) · [`registry.json`](registry.json) | `CollisionError` on duplicate `<bot>.<tool>` names; the registry is built from manifests, not hand-maintained |
| **Tested on a clean checkout** | [`tests/`](tests/) | **224 passed, 8 skipped, 0 failed** (Python 3.12); the 8 skips are real integration tests that no-op when external resources are absent |

> **Honesty rails:** `224` is the real, reproducible count on this checkout. mcp-factory generates the *scaffold* and runs the hub — it does not "generate the production server" or carry any client/CI claims. The hardened production layer (per-tool auth boundaries, the full failure set, two-axis version-pinning) is built per engagement on top of this engine. That applies to both Python scaffold styles below — see "Two Python styles" for exactly what the fastmcp variant does and doesn't add on top of that baseline.

## Quick Start

### Factory mode (generate config from one manifest)

```bash
# Reference an existing MCP server (no code generated — just the config entry)
python hub_server.py --manifest examples/fleet_health.yaml

# Scaffold a new MCP server from scratch
python hub_server.py --manifest my_bot/mcp.yaml --output-dir my_bot/

# Dry run — preview without writing
python hub_server.py --manifest my_bot/mcp.yaml --dry-run

# Self-verify: compare factory output to live ~/.claude.json entry
python hub_server.py --manifest examples/fleet_health.yaml --verify ~/.claude.json
```

Output always goes to `~/.claude.json.factory-test` by default — **never** to the live `~/.claude.json`. Copy entries manually after review.

### Scan mode (batch-register all bots)

```bash
# Dry-run diff: show what would change in ~/.claude.json
python hub_server.py --scan C:\path\to\projects

# Apply: write ~/.claude.json after backing it up
python hub_server.py --scan C:\path\to\projects --apply

# Force-update entries already registered
python hub_server.py --scan C:\path\to\projects --apply --force
```

`--scan` discovers all `projects/*/mcp.yaml` files, validates each, and diffs them against the current `~/.claude.json`. Default root is `C:\path\to\projects`. With `--apply`, a timestamped backup is created at `~/.claude.json.scan-backup-<timestamp>` before writing.

**Skip logic:** manifests whose name already exists in `~/.claude.json` are skipped unless `--force` is passed. This prevents accidentally overwriting manually-crafted entries.

### Serve mode (runtime hub — single MCP for all bots)

```bash
# Run the hub as a live MCP server (stdio transport)
python hub_server.py --serve

# Serve with multiple scan roots (repeatable)
python hub_server.py --serve \
  --scan-root C:\path\to\projects \
  --scan-root C:\path\to\Claude

# Register+serve in one step (scan --apply then start hub)
python hub_server.py --register \
  --scan-root C:\path\to\projects \
  --scan-root C:\path\to\Claude
```

The hub scans all `mcp.yaml` manifests under each `--scan-root` at startup, then exposes every bot's tools under the `<bot>.<tool>` namespace (e.g., `fleet-health.fleet_status`, `my-bot.run_scan`). Tools are proxied to per-bot subprocess MCP servers with lazy startup.

**Hub meta-tool:** `_hub.list_bots` returns the registered bots and their subprocess status.

Hub is pre-registered in `~/.claude.json` as `mcp-factory-hub` (see `scripts/register_hub.py`).

### Node.js template

Factory generates Node.js stubs when `runtime.type: node` is set in `mcp.yaml`:

```yaml
runtime:
  type: node
  command: "node"
  output: "path/to/server.js"
```

Generated stubs use `@modelcontextprotocol/sdk` with stdio transport and zod for argument validation. See `examples/node_example.yaml` for a working demo.

### Two Python styles: raw SDK vs. FastMCP

For `runtime.type: python`, the factory can scaffold either of two styles from the exact same manifest:

```yaml
runtime:
  type: python
  command: "python"
  style: raw       # default — official `mcp` SDK, hand-rolled list_tools/call_tool
  # style: fastmcp # FastMCP v2 (jlowin/fastmcp), decorator-based tool registration
```

Both styles read the same `tools:` / `env_required:` fields and produce a server that speaks the same stdio JSON-RPC wire protocol — the runtime hub's `SubprocessAdapter` proxies either one without any adapter changes (see `tests/test_fastmcp_template.py::TestFastmcpServeSmoke` for a live generate-and-call test).

| | `style: raw` (`python_server.py.j2`) | `style: fastmcp` (`python_fastmcp.j2`) |
|---|---|---|
| SDK | official `mcp` package, `mcp.server.Server` | `fastmcp` (pinned `fastmcp>=3.4.2`, tested against 3.4.2) |
| Tool registration | manual `@server.list_tools()` / `@server.call_tool()` dispatch | one `@mcp.tool(...)`-decorated function per tool |
| Arg schema | hand-built JSON Schema dict per arg | `Annotated[type, Field(description=...)]` on real Python parameters — FastMCP derives the JSON Schema, including required/optional, from the signature |
| Tool body | `# TODO: implement` stub | same stub, wrapped in `try/except Exception` — a runtime error in a filled-in implementation returns a structured `{"status": "error", ...}` instead of crashing the process |
| `env_required` | not enforced at scaffold level | rendered into a `_check_required_env()` startup check that warns to stderr if a declared var is missing — a presence check, not credential validation |

**Gaps, stated honestly:** neither style implements per-tool authorization, rate limiting, or the "full failure set" the hub-level `subprocess_adapter.py` gives you for free (typed errors, lazy start, `atexit` cleanup) — that's still a per-engagement build on top of either scaffold. The fastmcp template's fail-soft wrapper and env-presence check are new, real code (read `mcp_factory/templates/python_fastmcp.j2`), not a marketing claim about auth — they were added because FastMCP's decorator model made them cheap to include cleanly; they have not (yet) been backported to the raw template, which is why the two styles differ slightly in what ships out of the box. If your engagement needs FastMCP-specific features beyond this (resources, prompts, HTTP/SSE transport, middleware-based auth), the generated file is a normal FastMCP app — extend it directly.

See `examples/fastmcp_example.yaml` for a working demo manifest.

## mcp.yaml Schema

```yaml
name: my-bot                   # REQUIRED — unique MCP server name (key in claude.json)
description: >                 # REQUIRED — shown in Claude's tool descriptions
  What this bot does and when to use it.

runtime:                       # REQUIRED
  type: python                 # python | node | binary
  command: "C:\\Python314\\python.exe"  # full path to interpreter
  script: "path/to/server.py"  # existing server (skips scaffold generation)
  output: "path/to/out.py"     # where to write generated scaffold (omit = auto)
  style: raw                   # python only: raw (default) | fastmcp — see "Two Python styles"

tools:                         # REQUIRED — list of MCP tools to expose
  - name: tool_name            # REQUIRED
    description: >             # REQUIRED — used by Claude for routing
      What this tool does.
    args:                      # Optional list of arguments
      - name: arg_name         # REQUIRED
        type: string           # string | number | boolean | object | array
        required: true         # default: true
        description: "..."     # shown in Claude's tool schema

env_required:                  # env var names that must be set at runtime
  - MY_API_KEY

env:                           # static env vars injected into claude.json entry
  MY_API_KEY: ""               # leave value empty — fill in ~/.claude.json manually

tags: [trading, health]        # for documentation / future routing
priority: high                 # high | medium | low
```

### Key rules

- `runtime.script` + existing file → factory references it, skips scaffold
- `runtime.script` + missing file → validation error (use `runtime.output` for new scaffolds)
- `runtime.output` → explicit path for generated stub (absolute recommended)
- Neither `script` nor `output` → error at config-write step

## How to Add a New MCP

1. Write `mcp.yaml` at your bot repo root (or in `examples/`)
2. Run the factory:
   ```bash
   python hub_server.py --manifest path/to/mcp.yaml
   ```
3. Review `~/.claude.json.factory-test` — confirm the entry looks correct
4. Copy the entry into `~/.claude.json` under `mcpServers`
5. Restart Claude Code

If the bot has no existing server, the factory generates a stub at `generated/<name>_server.py`. Fill in the `# TODO: implement` sections and set `runtime.script` to the stub path for future runs.

## Runtime Hub Architecture

```
hub_server.py --serve
  └── mcp_factory/runtime/
      ├── hub.py               async MCP server (lists + routes all tools)
      ├── registry.py          maps <bot>.<tool> → manifest + adapter
      └── subprocess_adapter.py  spawns per-bot MCP server, proxies JSON-RPC
```

**Subprocess lifecycle:**
- Adapters start lazily on first tool call (no upfront spawn)
- Keep-alive for the hub session (one process per bot)
- `_hub.list_bots()` reports status: `idle` (not yet started) or `running`
- All adapters stopped via `atexit` on hub exit; `stop()` kills if needed after 5 s

**Tool naming:** `<bot-name>.<tool-name>` — hyphens preserved, dots as separator.
Example: `fleet-health.fleet_status`, `my-bot.get_alerts`.

## Day 4 — workflow_runner.py

Standalone CLI harness for research workflows, independent of `hub_server.py`.

```bash
# Discover and list all SKILL.md workflows
python -m mcp_factory.workflow_runner --list

# Run a specific workflow
python -m mcp_factory.workflow_runner --run my-skill

# Validate all discovered SKILL.md files
python -m mcp_factory.workflow_runner --validate

# Write/update registry.json from discovered skills
python -m mcp_factory.workflow_runner --write-registry

# Check for drift between discovered skills and registry.json
python -m mcp_factory.workflow_runner --check

# Control cache behavior
python -m mcp_factory.workflow_runner --run my-skill --cache-policy force-refresh
python -m mcp_factory.workflow_runner --run my-skill --cache-policy read-only
```

### How it works

`workflow_runner.py` scans `~/research` by default (override with `--scan-root`) for `SKILL.md` files containing YAML frontmatter. Each `SKILL.md` defines a named workflow with metadata:

```yaml
---
name: my-skill
description: What this workflow does
output_path_template: "~/vault/output/{date}/{name}.md"
---
Prompt body passed to claude -p subprocess...
```

- **Discover:** `git ls-files` to enumerate tracked `SKILL.md` files under each scan root
- **Validate:** checks required frontmatter fields (`name`, `description`)
- **Cache:** SHA-based cache keyed on prompt content; `auto` (default) skips re-run if output unchanged, `force-refresh` always re-runs, `read-only` never writes
- **Run:** invokes `claude -p <prompt>` as a subprocess, streams output
- **Write output:** expands `output_path_template`, writes result to vault
- **Registry:** `--write-registry` persists discovered skills to `registry.json`; `--check` detects drift between filesystem and registry without writing

## Directory Layout

```
mcp-factory/
├── hub_server.py              # CLI entry point (factory / scan / serve)
├── mcp_factory/
│   ├── manifest.py            # Manifest dataclass + YAML loader + validation
│   ├── generator.py           # Python MCP server stub scaffolder
│   ├── config.py              # claude.json entry builder + comparator
│   ├── scan.py                # --scan mode: manifest discovery + diff/apply
│   ├── workflow_runner.py     # Day 4: standalone CLI harness for SKILL.md workflows
│   ├── templates/              # packaged as data so `pip install` ships them too
│   │   ├── python_server.py.j2    # Jinja2 template — raw mcp SDK stubs (style: raw, default)
│   │   ├── python_fastmcp.j2      # Jinja2 template — FastMCP v2 stubs (style: fastmcp)
│   │   └── node_server.js.j2      # Jinja2 template for generated Node.js stubs
│   └── runtime/
│       ├── subprocess_adapter.py  # subprocess MCP client (JSON-RPC proxy)
│       ├── registry.py            # tool registry with collision detection
│       └── hub.py                 # async hub MCP server
├── tests/
│   ├── fixtures/
│   │   ├── fleet_health.yaml   # Day 1 self-verification fixture
│   │   ├── minimal.yaml        # Minimal valid manifest
│   │   └── mock_mcp_server.py  # Stdlib-only mock MCP server for adapter tests
│   ├── test_manifest.py
│   ├── test_generator.py
│   ├── test_subprocess_adapter.py
│   ├── test_registry.py
│   ├── test_scan.py
│   ├── test_hub_cli.py
│   ├── test_mcp_pkg.py
│   ├── test_node_template.py
│   ├── test_python_template.py
│   ├── test_fastmcp_template.py  # style: fastmcp generation + import + serve-smoke tests
│   ├── test_register_flag.py
│   ├── test_registration.py
│   ├── test_smoke_hub.py
│   ├── test_watcher.py
│   ├── test_workflow_runner.py  # Day 4: workflow_runner unit + integration tests
│   └── test_integration_fleet_health.py  # live integration tests (skipped if server absent)
├── examples/
│   ├── fleet_health.yaml      # Example manifest referencing an existing server
│   ├── node_example.yaml      # Example manifest for the node template
│   └── fastmcp_example.yaml   # Example manifest for the fastmcp template
└── pyproject.toml
```

## Self-Verification

The `examples/fleet_health.yaml` manifest references an example server. Running:

```bash
python hub_server.py --manifest examples/fleet_health.yaml --verify ~/.claude.json
```

confirms the factory produces a matching `~/.claude.json` entry.

## Running Tests

```bash
python -m pytest tests/ -v
```

On a clean checkout (Python 3.12), with `pip install -e .[dev]`: **224 passed, 8 skipped, 0 failed.**

The 8 skipped tests are real integration tests that need external resources and skip automatically when those are absent:
- `test_integration_fleet_health.py` (5 tests) requires a fleet-health `server.py` on disk (`FLEET_HEALTH_SERVER_PATH`).
- `test_node_template.py` (1 test) requires `node` and `@modelcontextprotocol/sdk` (`node_modules/`) to be present.
- `test_watcher.py` (2 tests) requires the `watchdog` package.

The fastmcp-style template tests (`test_fastmcp_template.py`) are not in this skip list — `fastmcp` is installed as a `[dev]` extra, so they run for real on a standard dev setup.


## Commercial support

Maintained by [Jaimen Bell](https://jaimenbell.dev). For production MCP integrations, custom servers, or agent-reliability work, see [jaimenbell.dev](https://jaimenbell.dev) or sponsor ongoing maintenance via [GitHub Sponsors](https://github.com/sponsors/jaimenbell).

<!-- MCP registry ownership marker -->
mcp-name: io.github.jaimenbell/mcp-factory
