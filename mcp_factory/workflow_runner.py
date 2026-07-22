"""Workflow runner — discover, validate, and execute research-harness SKILL.md workflows.

Discovers workflows from SKILL.md files on configured scan roots, validates their input
schemas, checks output caches, and executes them via subprocess.  Output paths are
resolved from each workflow's ``output_path_template`` frontmatter key; the optional
``VAULT_ROOT`` environment variable controls the base directory for relative paths.

Public API:
    discover(scan_roots=None) -> dict[str, dict]
    validate(workflow_name, inputs, registry=None) -> tuple[bool, list[str]]
    cache_check(workflow_name, inputs, registry=None) -> Path | None
    run(workflow_name, inputs, dry_run=False, registry=None) -> dict
    write_output(workflow_name, vault_path, content, sources) -> Path

CLI:
    python -m mcp_factory.workflow_runner --list
    python -m mcp_factory.workflow_runner --run <name> [--input k=v ...] [--dry-run]
    python -m mcp_factory.workflow_runner --validate <name> [--input k=v ...]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


HARNESS_VERSION = "0.1"

_HOME = Path.home()

DEFAULT_SCAN_ROOTS = [
    _HOME / "research",
]

VAULT_ROOT = Path(os.environ.get("VAULT_ROOT", str(_HOME / "vault")))

REGISTRY_PATH = Path(__file__).parent.parent / "registry.json"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@dataclass
class Workflow:
    name: str
    domain: str
    skill_md_path: Path
    frontmatter: dict[str, Any]
    body: str

    @property
    def cache_ttl_days(self) -> float:
        return float(self.frontmatter.get("cache_ttl_days", 0))

    @property
    def inputs_schema(self) -> dict[str, Any]:
        return self.frontmatter.get("inputs_schema", {}) or {}

    def to_registry_entry(self) -> dict[str, Any]:
        fm = self.frontmatter
        skill_md = _relative_to_research(self.skill_md_path)
        return {
            "name": self.name,
            "display_name": fm.get("display_name") or self.name.replace("-", " ").title(),
            "domain": self.domain,
            "version": str(fm.get("version", "0.0.0")),
            "harness_version": str(fm.get("harness_version", HARNESS_VERSION)),
            "description": (fm.get("description") or "").strip(),
            "skill_md": skill_md,
            "trigger_phrases": fm.get("trigger_phrases", []) or [],
            "cadence": fm.get("cadence", "as-needed"),
            "workflow_type": fm.get("workflow_type", "data-heavy"),
            "consumers": fm.get("consumers", ["human"]) or ["human"],
            "requires_worktree": bool(fm.get("requires_worktree", False)),
            "cache_ttl_days": float(fm.get("cache_ttl_days", 0)),
            "phase": int(fm.get("phase", 1)),
        }


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _relative_to_research(p: Path) -> str:
    """Return path relative to a 'research/' segment if found, else POSIX absolute.

    Strips everything up to and including the segment before 'research/' so the
    registry stays portable across checkouts and worktrees. Falls back to a
    POSIX-style absolute path if no 'research' segment exists.
    """
    parts = p.parts
    for i, seg in enumerate(parts):
        if seg == "research":
            return "/".join(parts[i:]).replace("\\", "/")
    return str(p).replace("\\", "/")


def _parse_skill_md(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter")
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: malformed frontmatter — {e}")
    body = text[m.end():]
    return fm, body


def _resolve_scan_roots(explicit: Optional[list[Path | str]] = None) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    env = os.environ.get("MCP_FACTORY_RESEARCH_ROOTS")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p]
    return list(DEFAULT_SCAN_ROOTS)


def _git_ls_files_skill_mds(root: Path) -> Optional[list[Path]]:
    """Return git-tracked SKILL.md paths under root, or None if root is not a git repo.

    Falls back to None (triggering rglob) when git is unavailable or the directory
    is not inside a git repo. Using git ls-files ensures the registry only tracks
    SKILL files committed to the main branch, excluding worktree-only work-in-progress.

    The repo-location ``GIT_*`` environment variables (set by git when it invokes
    hooks, or leaked by a parent process that ran under one) are stripped before the
    subprocess so git discovers the repository from ``cwd=root`` as intended.  Without
    this, an inherited ``GIT_DIR``/``GIT_WORK_TREE`` makes ``git ls-files`` answer for
    the leaked repo regardless of ``cwd`` — silently corrupting discovery and making
    the test suite non-deterministic depending on the caller's environment.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_COMMON_DIR",
            "GIT_PREFIX",
            "GIT_CEILING_DIRECTORIES",
        )
    }
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", "*/SKILL.md", "SKILL.md"],
            capture_output=True,
            text=True,
            cwd=root,
            env=env,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    paths: list[Path] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line and line.endswith("SKILL.md"):
            p = root / Path(line)
            if p.exists():
                paths.append(p)
    return paths


def discover(scan_roots: Optional[list[Path | str]] = None) -> dict[str, Workflow]:
    """Scan roots for SKILL.md files, parse frontmatter, return {name: Workflow}.

    Layout assumption: <root>/<domain>/<workflow-name>/SKILL.md

    Uses `git ls-files` when the scan root is inside a git repo so that only
    main-branch-tracked SKILL files appear. Falls back to filesystem rglob when
    git is unavailable (e.g. test fixtures in tmp_path).
    """
    roots = _resolve_scan_roots(scan_roots)
    found: dict[str, Workflow] = {}

    for root in roots:
        if not root.exists():
            continue
        skill_files = _git_ls_files_skill_mds(root)
        if skill_files is None:
            skill_files = list(root.rglob("SKILL.md"))
        for skill_path in skill_files:
            try:
                fm, body = _parse_skill_md(skill_path)
            except (ValueError, OSError) as e:
                print(f"[discover] skip {skill_path}: {e}", file=sys.stderr)
                continue

            name = fm.get("name")
            if not name:
                print(f"[discover] skip {skill_path}: missing 'name' field", file=sys.stderr)
                continue

            domain = fm.get("domain")
            if not domain:
                try:
                    domain = skill_path.parent.parent.name
                except Exception:
                    domain = "unknown"

            if name in found:
                print(
                    f"[discover] duplicate name '{name}': "
                    f"{found[name].skill_md_path} vs {skill_path} (keeping first)",
                    file=sys.stderr,
                )
                continue

            found[name] = Workflow(
                name=name,
                domain=str(domain),
                skill_md_path=skill_path,
                frontmatter=fm,
                body=body,
            )

    return found


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "string": str,
    "str": str,
    "number": (int, float),
    "int": int,
    "integer": int,
    "float": float,
    "boolean": bool,
    "bool": bool,
    "array": list,
    "list": list,
    "object": dict,
    "dict": dict,
}


def validate(
    workflow_name: str,
    inputs: dict[str, Any],
    registry: Optional[dict[str, Workflow]] = None,
) -> tuple[bool, list[str]]:
    """Check inputs against the workflow's inputs_schema.

    Returns (ok, errors). Unknown keys are warnings, not errors.
    """
    registry = registry if registry is not None else discover()
    if workflow_name not in registry:
        return False, [f"unknown workflow: {workflow_name}"]

    schema = registry[workflow_name].inputs_schema
    errors: list[str] = []

    for field_name, spec in schema.items():
        spec = spec or {}
        required = bool(spec.get("required", False))
        has_default = "default" in spec
        if field_name not in inputs:
            if required and not has_default:
                errors.append(f"missing required input: {field_name}")
            continue

        value = inputs[field_name]
        declared_type = spec.get("type")
        if declared_type and declared_type in _TYPE_MAP:
            expected = _TYPE_MAP[declared_type]
            if not isinstance(value, expected):
                errors.append(
                    f"input '{field_name}' expected {declared_type}, got {type(value).__name__}"
                )

    return (not errors), errors


def _resolve_inputs(workflow: Workflow, inputs: dict[str, Any]) -> dict[str, Any]:
    """Fill in defaults from the schema for any unspecified inputs."""
    resolved = dict(inputs)
    for field_name, spec in workflow.inputs_schema.items():
        spec = spec or {}
        if field_name not in resolved and "default" in spec:
            resolved[field_name] = spec["default"]
    return resolved


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _build_template_context(workflow: Workflow, inputs: dict[str, Any]) -> dict[str, Any]:
    """Build the variable context used to expand `output_path_template`.

    Includes all resolved inputs plus a small set of derived keys that workflows
    commonly need: `workflow_name`, `domain`, `date` (today, ISO), and
    `period_end` when an input named `period` is a `YYYY-MM-DD..YYYY-MM-DD`
    range or an empty/relative placeholder (then computed as next Sunday, to
    preserve postmortem semantics).
    """
    today = _dt.date.today()
    ctx: dict[str, Any] = dict(inputs)
    ctx.setdefault("workflow_name", workflow.name)
    ctx.setdefault("domain", workflow.domain)
    ctx.setdefault("date", today.isoformat())

    period = inputs.get("period")
    if isinstance(period, str) and ".." in period:
        ctx.setdefault("period_end", period.split("..", 1)[1].strip())
    elif "period_end" not in ctx:
        days_ahead = (6 - today.weekday()) % 7
        ctx["period_end"] = (today + _dt.timedelta(days=days_ahead)).isoformat()
    return ctx


def _expected_output_path(workflow: Workflow, inputs: dict[str, Any]) -> Optional[Path]:
    """Resolve the workflow's cache-hit path.

    Preferred source: `output_path_template` in SKILL.md frontmatter, expanded
    with the context from `_build_template_context()`. Relative templates are
    rooted at `VAULT_ROOT`; absolute templates are honored as-is.

    Fallback (back-compat): the hardcoded postmortem path used by the MVP. Any
    other workflow without a template returns None (cache miss).
    """
    template = workflow.frontmatter.get("output_path_template")
    if template:
        ctx = _build_template_context(workflow, inputs)
        try:
            rendered = str(template).format(**ctx)
        except KeyError as e:
            print(
                f"[cache] {workflow.name}: output_path_template missing key {e}; "
                f"available: {sorted(ctx.keys())}",
                file=sys.stderr,
            )
            return None
        p = Path(rendered)
        return p if p.is_absolute() else VAULT_ROOT / p

    return None


def cache_check(
    workflow_name: str,
    inputs: dict[str, Any],
    registry: Optional[dict[str, Workflow]] = None,
) -> Optional[Path]:
    """Return the cached vault path if cache is fresh, else None.

    Per master plan §3.7. TTL comes from SKILL.md frontmatter; 0 means always-fresh
    (no cache). Output path mapping is workflow-specific (see _expected_output_path).
    """
    registry = registry if registry is not None else discover()
    if workflow_name not in registry:
        return None

    wf = registry[workflow_name]
    ttl = wf.cache_ttl_days
    if ttl <= 0:
        return None

    out = _expected_output_path(wf, _resolve_inputs(wf, inputs))
    if out is None or not out.exists():
        return None

    age_seconds = _dt.datetime.now().timestamp() - out.stat().st_mtime
    if age_seconds < ttl * 86400:
        return out
    return None


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------

def write_output(
    workflow_name: str,
    vault_path: Path | str,
    content: str,
    sources: list[str],
    extra_frontmatter: Optional[dict[str, Any]] = None,
) -> Path:
    """Write workflow output under VAULT_ROOT with standardized frontmatter.

    Per master plan §3.4. Path is created if missing. Existing files are
    overwritten (workflow re-runs replace prior output by design — the caller
    handles the cache decision via cache_check).
    """
    out = Path(vault_path)
    if not out.is_absolute():
        out = VAULT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    fm: dict[str, Any] = {
        "title": (extra_frontmatter or {}).get("title", workflow_name),
        "type": "research-output",
        "workflow": workflow_name,
        "generated_by": (extra_frontmatter or {}).get("generated_by", "claude-opus-4-7"),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sources": sources,
        "tags": (extra_frontmatter or {}).get(
            "tags", [f"workflow/{workflow_name}", "output-type/research"]
        ),
    }
    if extra_frontmatter:
        for k, v in extra_frontmatter.items():
            if k not in fm:
                fm[k] = v

    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = content if content.endswith("\n") else content + "\n"
    out.write_text(f"---\n{fm_text}\n---\n\n{body}", encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _build_prompt(workflow: Workflow, inputs: dict[str, Any]) -> tuple[str, str]:
    """Assemble (system_prompt, user_prompt) from SKILL.md + inputs."""
    system = workflow.body.strip()
    user_payload = {
        "workflow": workflow.name,
        "invoked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "invoked_by": "user:workflow_runner",
        "inputs": inputs,
        "cache_policy": "auto",
        "harness_version": HARNESS_VERSION,
    }
    user = (
        f"Run the **{workflow.name}** workflow per the contract in your system prompt.\n\n"
        f"Inputs:\n```json\n{json.dumps(user_payload, indent=2, default=str)}\n```\n\n"
        f"Conform to the harness contract: READ → EXECUTE → WRITE → CITE → OUTPUT. "
        f"Return the OUTPUT JSON shape per master plan §3.6 at the end of your response."
    )
    return system, user


def _load_anthropic_key() -> Optional[str]:
    """Load ANTHROPIC_API_KEY from ~/.env without logging the value."""
    env_path = _HOME / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            return val or None
    return None


def _invoke_claude_cli(system: str, user: str) -> tuple[int, str, str]:
    """Spawn `claude -p` with the prompt. Returns (rc, stdout, stderr)."""
    claude = shutil.which("claude")
    if not claude:
        return 127, "", "claude CLI not on PATH"
    full_prompt = f"<system>\n{system}\n</system>\n\n{user}"
    proc = subprocess.run(
        [claude, "-p", full_prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


CACHE_POLICIES = ("auto", "force-refresh", "read-only")


def run(
    workflow_name: str,
    inputs: dict[str, Any],
    dry_run: bool = False,
    registry: Optional[dict[str, Workflow]] = None,
    cache_policy: str = "auto",
) -> dict[str, Any]:
    """Execute the workflow.

    dry_run=True: emit the resolved system+user prompt to stdout, no LLM call.
    dry_run=False: spawn `claude -p` if available, else fall back to a noop with
    instructions for the caller (Anthropic API direct call is left to a future
    increment to keep the MVP tight).

    cache_policy:
        "auto"          — return cache hit if fresh, else execute (default).
        "force-refresh" — skip cache check, always execute.
        "read-only"     — return cache hit if fresh, else error (no execution).
    """
    if cache_policy not in CACHE_POLICIES:
        return {
            "status": "error",
            "error": f"invalid cache_policy: {cache_policy!r}; expected one of {list(CACHE_POLICIES)}",
        }

    registry = registry if registry is not None else discover()
    if workflow_name not in registry:
        return {"status": "error", "error": f"unknown workflow: {workflow_name}"}

    wf = registry[workflow_name]
    resolved = _resolve_inputs(wf, inputs)

    ok, errors = validate(workflow_name, resolved, registry=registry)
    if not ok:
        return {"status": "error", "error": "validation failed", "errors": errors}

    if cache_policy != "force-refresh":
        cached = cache_check(workflow_name, resolved, registry=registry)
        if cached is not None:
            return {
                "status": "ok",
                "cache_used": True,
                "cache_policy": cache_policy,
                "output_paths": [str(cached)],
                "summary": f"Cache hit (TTL {wf.cache_ttl_days}d): {cached}",
            }
        if cache_policy == "read-only":
            return {
                "status": "error",
                "error": "cache miss under read-only policy",
                "cache_policy": cache_policy,
                "workflow": workflow_name,
            }

    system, user = _build_prompt(wf, resolved)

    if dry_run:
        sys.stdout.write("===== SYSTEM PROMPT =====\n")
        sys.stdout.write(system + "\n\n")
        sys.stdout.write("===== USER PROMPT =====\n")
        sys.stdout.write(user + "\n")
        sys.stdout.flush()
        return {
            "status": "ok",
            "dry_run": True,
            "cache_policy": cache_policy,
            "system_prompt_len": len(system),
            "user_prompt_len": len(user),
            "resolved_inputs": resolved,
        }

    rc, out, err = _invoke_claude_cli(system, user)
    if rc != 0:
        return {
            "status": "error",
            "error": f"claude CLI exited {rc}",
            "stderr": err,
            "stdout": out,
        }

    written_path: Optional[Path] = None
    out_path = _expected_output_path(wf, resolved)
    if out_path is not None and out:
        try:
            written_path = write_output(workflow_name, out_path, out, sources=[])
        except OSError as e:
            return {
                "status": "error",
                "error": f"write_output failed: {e}",
                "cache_policy": cache_policy,
                "raw_output": out,
            }

    result: dict[str, Any] = {
        "status": "ok",
        "cache_used": False,
        "cache_policy": cache_policy,
        "raw_output": out,
        "stderr_warnings": err if err else None,
    }
    if written_path is not None:
        result["output_paths"] = [str(written_path)]
    return result


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def _build_registry_payload(
    scan_roots: Optional[list[Path | str]] = None,
    extra_entries: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Compute the registry.json payload (without writing)."""
    found = discover(scan_roots)
    entries = [wf.to_registry_entry() for wf in found.values()]
    if extra_entries:
        seen = {e["name"] for e in entries}
        for e in extra_entries:
            if e.get("name") not in seen:
                entries.append(e)
    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "research_workflows": entries,
    }


def write_registry(
    registry_path: Path | str = REGISTRY_PATH,
    scan_roots: Optional[list[Path | str]] = None,
    extra_entries: Optional[list[dict[str, Any]]] = None,
) -> Path:
    """Write the discovered workflows to registry.json (master plan §4.5)."""
    payload = _build_registry_payload(scan_roots, extra_entries)
    out = Path(registry_path)
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return out


def _strip_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that change every run so two payloads can be compared structurally."""
    return {k: v for k, v in payload.items() if k != "generated_at"}


def check_registry(
    registry_path: Path | str = REGISTRY_PATH,
    scan_roots: Optional[list[Path | str]] = None,
    extra_entries: Optional[list[dict[str, Any]]] = None,
) -> tuple[bool, str]:
    """Compare the on-disk registry to the discovered payload.

    Returns (in_sync, diff_text). `generated_at` is ignored — it changes every
    run. Designed for CI / pre-commit use: exit 0 on match, exit 1 with diff
    when discovery differs from the committed registry.
    """
    new = _strip_volatile(_build_registry_payload(scan_roots, extra_entries))
    path = Path(registry_path)
    if not path.exists():
        return False, f"registry.json not found at {path}"

    try:
        current = _strip_volatile(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"failed to read {path}: {e}"

    if current == new:
        return True, ""

    diff = "\n".join(
        difflib.unified_diff(
            json.dumps(current, indent=2, sort_keys=True).splitlines(),
            json.dumps(new, indent=2, sort_keys=True).splitlines(),
            fromfile=f"a/{path.name}",
            tofile="b/discovered",
            lineterm="",
        )
    )
    return False, diff


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_kv(items: list[str]) -> dict[str, Any]:
    """Parse repeated --input k=v args. Tries JSON first, falls back to string."""
    result: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--input expects k=v, got: {item}")
        k, v = item.split("=", 1)
        try:
            result[k] = json.loads(v)
        except json.JSONDecodeError:
            result[k] = v
    return result


def _cli(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    p = argparse.ArgumentParser(
        prog="python -m mcp_factory.workflow_runner",
        description="Research-harness workflow runner — discover and execute SKILL.md workflows.",
    )
    p.add_argument("--list", action="store_true", help="List discovered workflows")
    p.add_argument("--run", metavar="NAME", help="Run a workflow by name")
    p.add_argument("--validate", metavar="NAME", help="Validate inputs against a workflow")
    p.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="k=v",
        help="Input key=value (repeatable, JSON-parsed if possible)",
    )
    p.add_argument("--dry-run", action="store_true", help="(--run) emit resolved prompt only")
    p.add_argument(
        "--cache-policy",
        choices=CACHE_POLICIES,
        default="auto",
        help=(
            "(--run) cache behavior: auto (use cache if fresh, else execute), "
            "force-refresh (always execute), read-only (use cache or error)"
        ),
    )
    p.add_argument(
        "--scan-root",
        action="append",
        default=None,
        metavar="PATH",
        help="Override scan root (repeatable). Default: ~/research",
    )
    p.add_argument(
        "--write-registry",
        nargs="?",
        const=str(REGISTRY_PATH),
        metavar="PATH",
        help=f"Write discovered workflows to registry.json (default: {REGISTRY_PATH})",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help=(
            "(--write-registry) dry run — compare discovered to on-disk registry "
            "and exit 0 if in sync, 1 with a unified diff if drift is detected. "
            "Does not write."
        ),
    )

    args = p.parse_args(argv)

    registry = discover(args.scan_root)

    if args.list:
        if not registry:
            print("No workflows found.")
            return 0
        print(f"Discovered {len(registry)} workflow(s):\n")
        for name, wf in sorted(registry.items()):
            desc = (wf.frontmatter.get("description") or "").strip().replace("\n", " ")
            print(f"  {name}  ({wf.domain}, ttl={wf.cache_ttl_days}d)")
            print(f"    {desc[:100]}{'...' if len(desc) > 100 else ''}")
            print(f"    skill: {wf.skill_md_path}")
        return 0

    if args.write_registry:
        if args.check:
            in_sync, diff = check_registry(
                args.write_registry, scan_roots=args.scan_root
            )
            if in_sync:
                print(f"OK: {args.write_registry} is in sync ({len(registry)} workflows)")
                return 0
            print(f"DRIFT: {args.write_registry} differs from discovery", file=sys.stderr)
            if diff:
                print(diff, file=sys.stderr)
            return 1
        out = write_registry(args.write_registry, scan_roots=args.scan_root)
        print(f"Wrote {out} ({len(registry)} workflows)")
        return 0

    if args.validate:
        inputs = _parse_kv(args.input)
        ok, errors = validate(args.validate, inputs, registry=registry)
        print(json.dumps({"ok": ok, "errors": errors}, indent=2))
        return 0 if ok else 1

    if args.run:
        inputs = _parse_kv(args.input)
        result = run(
            args.run,
            inputs,
            dry_run=args.dry_run,
            registry=registry,
            cache_policy=args.cache_policy,
        )
        print("\n===== RESULT =====")
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("status") == "ok" else 1

    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
