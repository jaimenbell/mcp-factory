---
title: MCP Factory
type: project-readme
tags: [mcp, factory, claude]
---

# MCP Factory

Manifest-driven scaffolder and runtime hub for Claude MCP servers. Write a `mcp.yaml` for a bot repo; the factory generates the server stub and the `~/.claude.json` entry, or run the hub to serve all bots' tools through a single MCP endpoint.

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
│   └── runtime/
│       ├── subprocess_adapter.py  # subprocess MCP client (JSON-RPC proxy)
│       ├── registry.py            # tool registry with collision detection
│       └── hub.py                 # async hub MCP server
├── templates/
│   └── python_server.py.j2    # Jinja2 template for generated stubs
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
│   ├── test_register_flag.py
│   ├── test_registration.py
│   ├── test_smoke_hub.py
│   ├── test_watcher.py
│   ├── test_workflow_runner.py  # Day 4: workflow_runner unit + integration tests
│   └── test_integration_fleet_health.py  # live integration tests (skipped if server absent)
├── examples/
│   └── fleet_health.yaml      # Example manifest referencing an existing server
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

On a clean checkout (Python 3.12): **152 passed, 12 skipped, 0 failed.**

The 12 skipped tests are real integration tests that need external resources and skip automatically when those are absent:
- `test_integration_fleet_health.py` requires a fleet-health `server.py` on disk.
- `test_smoke_hub.py` spawns the hub and needs discoverable `mcp.yaml` manifests — set `MCP_FACTORY_SMOKE_ROOTS` (os.pathsep-separated scan roots) to exercise them.
