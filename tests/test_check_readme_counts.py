"""Tests for scripts/check_readme_counts.py (the CI count-verification gate).

The gate compares README test-count claims against a live pytest run's
summary line. TDD'd with fixture READMEs covering match / drift / missing.
Exercised via subprocess so the CLI contract (exit codes) is what's tested:
  0 = all claims match the live run
  1 = drift (a claim disagrees with the live run)
  2 = missing (no claims found in README, or no summary in pytest output)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_readme_counts.py"

PYTEST_OUTPUT_OK = "........\n216 passed, 8 skipped in 11.20s\n"

README_MATCH = """\
# MCP Factory

![tests](https://img.shields.io/badge/tests-216%20passing-brightgreen)

> Verifiable below (`python -m pytest tests/` -> **216 passed, 8 skipped**).

| **Tested** | **216 passed, 8 skipped, 0 failed** (Python 3.12) |
"""

README_DRIFT = """\
# MCP Factory

![tests](https://img.shields.io/badge/tests-187%20passing-brightgreen)

> Verifiable below (`python -m pytest tests/` -> **187 passed, 8 skipped**).
"""

README_MISSING = """\
# MCP Factory

No test counts are claimed anywhere in this document.
"""


def run_gate(tmp_path, readme_text, pytest_output_text):
    readme = tmp_path / "README.md"
    readme.write_text(readme_text, encoding="utf-8")
    out_file = tmp_path / "pytest-output.txt"
    out_file.write_text(pytest_output_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(out_file), "--readme", str(readme)],
        capture_output=True,
        text=True,
    )


class TestGateExitCodes:
    def test_match_exits_zero(self, tmp_path):
        result = run_gate(tmp_path, README_MATCH, PYTEST_OUTPUT_OK)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout

    def test_drift_exits_one(self, tmp_path):
        result = run_gate(tmp_path, README_DRIFT, PYTEST_OUTPUT_OK)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "DRIFT" in result.stdout

    def test_missing_claims_exits_two(self, tmp_path):
        result = run_gate(tmp_path, README_MISSING, PYTEST_OUTPUT_OK)
        assert result.returncode == 2, result.stdout + result.stderr

    def test_missing_summary_exits_two(self, tmp_path):
        result = run_gate(tmp_path, README_MATCH, "no summary line here\n")
        assert result.returncode == 2, result.stdout + result.stderr


class TestClaimParsing:
    def test_badge_only_drift_detected(self, tmp_path):
        readme = (
            "![tests](https://img.shields.io/badge/tests-999%20passing-x)\n"
            "**216 passed, 8 skipped**\n"
        )
        result = run_gate(tmp_path, readme, PYTEST_OUTPUT_OK)
        assert result.returncode == 1

    def test_skipped_count_drift_detected(self, tmp_path):
        readme = "**216 passed, 9 skipped**\n"
        result = run_gate(tmp_path, readme, PYTEST_OUTPUT_OK)
        assert result.returncode == 1

    def test_summary_without_skips_parses(self, tmp_path):
        readme = "**53 passed, 0 skipped**\n"
        result = run_gate(tmp_path, readme, "53 passed in 3.00s\n")
        assert result.returncode == 0

    def test_last_summary_line_wins(self, tmp_path):
        # Earlier numbers in the log (e.g. quoted docs) must not shadow the
        # real final summary.
        output = "some log: 5 passed earlier\n216 passed, 8 skipped in 12.26s\n"
        result = run_gate(tmp_path, README_MATCH, output)
        assert result.returncode == 0
