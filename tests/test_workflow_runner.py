"""Smoke tests for mcp_factory.workflow_runner — Phase 3 MVP.

Coverage:
    - discover() finds a SKILL.md under a fixture root.
    - validate() honors required + type from inputs_schema.
    - cache_check() returns None when output absent and respects ttl=0.
    - run(dry_run=True) emits a resolved prompt and reports lengths.
    - write_output() writes standardized frontmatter + body.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from mcp_factory import workflow_runner as wfr


SKILL_FIXTURE = textwrap.dedent("""\
    ---
    name: smoke-workflow
    description: Test fixture for workflow_runner.
    domain: test
    workflow_type: data-heavy
    cadence: as-needed
    version: 0.1.0
    harness_version: "0.1"
    cache_ttl_days: 0
    requires_worktree: false
    trigger_phrases:
      - smoke
    inputs_schema:
      ticker:
        type: string
        required: true
        description: Required string input.
      count:
        type: number
        default: 5
        description: Optional with default.
    output_schema:
      status: ok | error
    ---

    # Smoke workflow

    Body content used as the system prompt.
    """)


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "research" / "test" / "smoke-workflow"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_FIXTURE, encoding="utf-8")
    return tmp_path / "research"


def test_discover_finds_skill(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    assert "smoke-workflow" in reg
    wf = reg["smoke-workflow"]
    assert wf.domain == "test"
    assert wf.cache_ttl_days == 0


def test_validate_passes_with_required(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    ok, errors = wfr.validate("smoke-workflow", {"ticker": "AAPL"}, registry=reg)
    assert ok is True
    assert errors == []


def test_validate_fails_missing_required(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    ok, errors = wfr.validate("smoke-workflow", {}, registry=reg)
    assert ok is False
    assert any("ticker" in e for e in errors)


def test_validate_fails_type_mismatch(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    ok, errors = wfr.validate("smoke-workflow", {"ticker": 123}, registry=reg)
    assert ok is False
    assert any("ticker" in e and "string" in e for e in errors)


def test_validate_unknown_workflow(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    ok, errors = wfr.validate("nonexistent", {}, registry=reg)
    assert ok is False
    assert any("nonexistent" in e for e in errors)


def test_cache_check_zero_ttl_returns_none(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    assert wfr.cache_check("smoke-workflow", {"ticker": "X"}, registry=reg) is None


def test_run_dry_run_emits_prompt(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = wfr.run(
            "smoke-workflow",
            {"ticker": "AAPL"},
            dry_run=True,
            registry=reg,
        )
    out = buf.getvalue()
    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["resolved_inputs"] == {"ticker": "AAPL", "count": 5}
    assert "===== SYSTEM PROMPT =====" in out
    assert "===== USER PROMPT =====" in out
    assert "smoke-workflow" in out
    assert "AAPL" in out


def test_run_validation_error_blocks_execution(fixture_root: Path):
    reg = wfr.discover([fixture_root])
    result = wfr.run("smoke-workflow", {}, dry_run=True, registry=reg)
    assert result["status"] == "error"
    assert "validation" in result["error"]


def test_write_output_creates_frontmatter(tmp_path: Path):
    out = tmp_path / "sub" / "note.md"
    written = wfr.write_output(
        "smoke-workflow",
        out,
        "## body\n\nfoo",
        sources=["src1", "src2"],
        extra_frontmatter={"title": "Smoke", "tags": ["workflow/smoke-workflow"]},
    )
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "title: Smoke" in text
    assert "workflow: smoke-workflow" in text
    assert "type: research-output" in text
    assert "src1" in text
    assert "## body" in text


def _write_skill(root: Path, name: str, frontmatter_extra: str = "", ttl: float = 0) -> Path:
    """Helper: write a SKILL.md under root/test/<name>/SKILL.md with optional extras."""
    skill_dir = root / "test" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    text = (
        f"---\n"
        f"name: {name}\n"
        f"description: Fixture.\n"
        f"domain: test\n"
        f"workflow_type: data-heavy\n"
        f"cadence: as-needed\n"
        f"version: 0.1.0\n"
        f'harness_version: "0.1"\n'
        f"cache_ttl_days: {ttl}\n"
        f"requires_worktree: false\n"
        f"inputs_schema:\n"
        f"  ticker:\n"
        f"    type: string\n"
        f"    required: false\n"
        f"    default: ABC\n"
        f"{frontmatter_extra}"
        f"---\n\n"
        f"# {name}\n\nBody.\n"
    )
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir / "SKILL.md"


def test_output_path_template_resolves_with_inputs(tmp_path: Path):
    root = tmp_path / "research"
    _write_skill(
        root,
        "templated",
        frontmatter_extra='output_path_template: "Research/{workflow_name}/{ticker}-{date}.md"\n',
    )
    reg = wfr.discover([root])
    wf = reg["templated"]
    out = wfr._expected_output_path(wf, {"ticker": "WWD"})
    assert out is not None
    today = _dt.date.today().isoformat()
    assert out.name == f"WWD-{today}.md"
    assert "Research" in out.parts and "templated" in out.parts


def test_output_path_template_missing_key_returns_none(tmp_path: Path, capsys):
    root = tmp_path / "research"
    _write_skill(
        root,
        "missing-key",
        frontmatter_extra='output_path_template: "Research/{nonexistent}.md"\n',
    )
    reg = wfr.discover([root])
    out = wfr._expected_output_path(reg["missing-key"], {})
    assert out is None
    err = capsys.readouterr().err
    assert "output_path_template" in err
    assert "nonexistent" in err


def test_output_path_template_absolute_path_honored(tmp_path: Path):
    root = tmp_path / "research"
    abs_target = (tmp_path / "absolute" / "out.md").as_posix()
    _write_skill(
        root,
        "abs-template",
        frontmatter_extra=f'output_path_template: "{abs_target}"\n',
    )
    reg = wfr.discover([root])
    out = wfr._expected_output_path(reg["abs-template"], {})
    assert out is not None
    assert out.is_absolute()
    assert out.as_posix() == abs_target


def test_output_path_template_period_end_derived(tmp_path: Path):
    root = tmp_path / "research"
    _write_skill(
        root,
        "period-end",
        frontmatter_extra='output_path_template: "Reports/{period_end}.md"\n',
    )
    reg = wfr.discover([root])
    out = wfr._expected_output_path(
        reg["period-end"], {"period": "2026-04-27..2026-05-03"}
    )
    assert out is not None
    assert out.name == "2026-05-03.md"


def test_postmortem_no_template_returns_none(tmp_path: Path):
    """Without output_path_template, any workflow (including paper-trading-postmortem) returns None."""
    root = tmp_path / "research"
    _write_skill(root, "paper-trading-postmortem", ttl=6)
    reg = wfr.discover([root])
    out = wfr._expected_output_path(
        reg["paper-trading-postmortem"], {"period": "2026-04-27..2026-05-03"}
    )
    assert out is None


def _setup_cache_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, dict]:
    """Build a SKILL with TTL>0 + output_path_template, write a fake cache file.

    Returns (research_root, registry). VAULT_ROOT is monkeypatched to tmp_path
    so the template resolves under tmp.
    """
    monkeypatch.setattr(wfr, "VAULT_ROOT", tmp_path / "vault")
    root = tmp_path / "research"
    _write_skill(
        root,
        "cached-wf",
        frontmatter_extra='output_path_template: "out/{ticker}.md"\n',
        ttl=7,
    )
    reg = wfr.discover([root])
    cache_file = tmp_path / "vault" / "out" / "ABC.md"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("cached content", encoding="utf-8")
    return root, reg


def test_run_cache_policy_auto_returns_cache_hit(tmp_path: Path, monkeypatch):
    _, reg = _setup_cache_fixture(tmp_path, monkeypatch)
    result = wfr.run("cached-wf", {}, dry_run=True, registry=reg, cache_policy="auto")
    assert result["status"] == "ok"
    assert result.get("cache_used") is True
    assert result["cache_policy"] == "auto"


def test_run_cache_policy_force_refresh_skips_cache(tmp_path: Path, monkeypatch):
    _, reg = _setup_cache_fixture(tmp_path, monkeypatch)
    result = wfr.run(
        "cached-wf", {}, dry_run=True, registry=reg, cache_policy="force-refresh"
    )
    assert result["status"] == "ok"
    assert result.get("cache_used") is None  # never set on dry-run path
    assert result["dry_run"] is True
    assert result["cache_policy"] == "force-refresh"


def test_run_cache_policy_read_only_errors_on_miss(tmp_path: Path, monkeypatch):
    """Read-only with a TTL=0 workflow always misses → error."""
    monkeypatch.setattr(wfr, "VAULT_ROOT", tmp_path / "vault")
    root = tmp_path / "research"
    _write_skill(root, "miss-wf", ttl=0)
    reg = wfr.discover([root])
    result = wfr.run(
        "miss-wf", {}, dry_run=True, registry=reg, cache_policy="read-only"
    )
    assert result["status"] == "error"
    assert "cache miss" in result["error"]
    assert result["cache_policy"] == "read-only"


def test_run_cache_policy_read_only_returns_hit(tmp_path: Path, monkeypatch):
    _, reg = _setup_cache_fixture(tmp_path, monkeypatch)
    result = wfr.run(
        "cached-wf", {}, dry_run=True, registry=reg, cache_policy="read-only"
    )
    assert result["status"] == "ok"
    assert result.get("cache_used") is True


def test_run_cache_policy_invalid_value_returns_error():
    result = wfr.run("anything", {}, cache_policy="bogus")
    assert result["status"] == "error"
    assert "invalid cache_policy" in result["error"]


def test_check_registry_in_sync(tmp_path: Path):
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    registry_path = tmp_path / "registry.json"
    wfr.write_registry(registry_path, scan_roots=[root])

    in_sync, diff = wfr.check_registry(registry_path, scan_roots=[root])
    assert in_sync is True
    assert diff == ""


def test_check_registry_drift_returns_diff(tmp_path: Path):
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    registry_path = tmp_path / "registry.json"
    wfr.write_registry(registry_path, scan_roots=[root])

    # Add a second workflow → registry on disk is now stale.
    _write_skill(root, "wf-b")

    in_sync, diff = wfr.check_registry(registry_path, scan_roots=[root])
    assert in_sync is False
    assert "wf-b" in diff
    assert diff.startswith("---")  # unified diff header


def test_check_registry_missing_file(tmp_path: Path):
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    in_sync, diff = wfr.check_registry(tmp_path / "nope.json", scan_roots=[root])
    assert in_sync is False
    assert "not found" in diff


def test_check_registry_ignores_generated_at(tmp_path: Path):
    """A registry written 'long ago' but with the same workflows should still be in sync."""
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    registry_path = tmp_path / "registry.json"
    wfr.write_registry(registry_path, scan_roots=[root])

    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["generated_at"] = "2020-01-01T00:00:00+00:00"
    registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    in_sync, _ = wfr.check_registry(registry_path, scan_roots=[root])
    assert in_sync is True


def test_cli_check_exits_0_when_in_sync(tmp_path: Path, capsys):
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    registry_path = tmp_path / "registry.json"
    wfr.write_registry(registry_path, scan_roots=[root])

    rc = wfr._cli([
        "--write-registry", str(registry_path),
        "--check",
        "--scan-root", str(root),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "in sync" in out


def test_cli_check_exits_1_with_diff_when_drift(tmp_path: Path, capsys):
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    registry_path = tmp_path / "registry.json"
    wfr.write_registry(registry_path, scan_roots=[root])

    _write_skill(root, "wf-b")
    rc = wfr._cli([
        "--write-registry", str(registry_path),
        "--check",
        "--scan-root", str(root),
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "DRIFT" in err
    assert "wf-b" in err


def test_cli_check_does_not_write(tmp_path: Path):
    """--check must not mutate the registry on disk, even on drift."""
    root = tmp_path / "research"
    _write_skill(root, "wf-a")
    registry_path = tmp_path / "registry.json"
    wfr.write_registry(registry_path, scan_roots=[root])
    original_mtime = registry_path.stat().st_mtime
    original_text = registry_path.read_text(encoding="utf-8")

    _write_skill(root, "wf-b")
    wfr._cli([
        "--write-registry", str(registry_path),
        "--check",
        "--scan-root", str(root),
    ])

    assert registry_path.stat().st_mtime == original_mtime
    assert registry_path.read_text(encoding="utf-8") == original_text


def test_run_writes_output_after_execution(tmp_path: Path, monkeypatch):
    """run() calls write_output() after a successful claude CLI invocation (BUG-1 regression)."""
    monkeypatch.setattr(wfr, "VAULT_ROOT", tmp_path / "vault")
    root = tmp_path / "research"
    _write_skill(
        root,
        "write-test",
        frontmatter_extra='output_path_template: "out/{ticker}.md"\n',
        ttl=1,
    )
    reg = wfr.discover([root])

    monkeypatch.setattr(
        wfr, "_invoke_claude_cli", lambda s, u: (0, "# Result\n\nContent.", "")
    )
    result = wfr.run("write-test", {"ticker": "AAPL"}, registry=reg)

    assert result["status"] == "ok"
    assert result.get("cache_used") is False
    assert "output_paths" in result, "run() must populate output_paths after write"
    out = Path(result["output_paths"][0])
    assert out.exists(), f"vault file not created at {out}"
    text = out.read_text(encoding="utf-8")
    assert "Content." in text
    assert "workflow: write-test" in text


def test_run_no_output_path_template_omits_output_paths(tmp_path: Path, monkeypatch):
    """run() succeeds without output_paths when workflow has no output_path_template."""
    monkeypatch.setattr(wfr, "VAULT_ROOT", tmp_path / "vault")
    root = tmp_path / "research"
    _write_skill(root, "no-template-wf", ttl=0)
    reg = wfr.discover([root])

    monkeypatch.setattr(
        wfr, "_invoke_claude_cli", lambda s, u: (0, "output here", "")
    )
    result = wfr.run("no-template-wf", {}, registry=reg)

    assert result["status"] == "ok"
    assert "output_paths" not in result


def test_discover_uses_git_ls_files_when_available(tmp_path: Path, monkeypatch):
    """discover() uses git ls-files result when available, excluding untracked files (BUG-2 regression)."""
    root = tmp_path / "research"
    _write_skill(root, "tracked-wf")
    _write_skill(root, "untracked-wf")

    tracked_path = root / "test" / "tracked-wf" / "SKILL.md"

    monkeypatch.setattr(wfr, "_git_ls_files_skill_mds", lambda r: [tracked_path])
    reg = wfr.discover([root])

    assert "tracked-wf" in reg
    assert "untracked-wf" not in reg, "untracked SKILL.md must not appear in discover() output"


def test_discover_falls_back_to_rglob_when_not_git(tmp_path: Path, monkeypatch):
    """discover() falls back to rglob when git ls-files returns None (non-git dir)."""
    root = tmp_path / "research"
    _write_skill(root, "any-wf")

    monkeypatch.setattr(wfr, "_git_ls_files_skill_mds", lambda r: None)
    reg = wfr.discover([root])

    assert "any-wf" in reg


def test_git_ls_files_returns_none_for_non_git_dir(tmp_path: Path):
    """_git_ls_files_skill_mds returns None for a directory not in a git repo."""
    result = wfr._git_ls_files_skill_mds(tmp_path)
    assert result is None


def test_relative_to_research_strips_prefix():
    p = Path("C:/Users/owner/projects/my-skills/research/bot-ops/x/SKILL.md")
    assert wfr._relative_to_research(p) == "research/bot-ops/x/SKILL.md"


def test_relative_to_research_handles_worktree():
    p = Path(
        "C:/Users/owner/projects/my-skills/.claude/worktrees/x/research/visual/y/SKILL.md"
    )
    assert wfr._relative_to_research(p) == "research/visual/y/SKILL.md"
