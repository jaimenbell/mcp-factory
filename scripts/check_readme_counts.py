"""CI count-verification gate: README test-count claims vs the live pytest run.

Stdlib-only. Parses this repo's actual README count phrasings:

  1. shields.io badge:   tests-<N>%20passing          (passed count only)
  2. bold inline claim:  **<N> passed, <M> skipped**  (optionally ", 0 failed")

and compares every claim against the final summary line of a captured
``pytest -q`` output file (``<N> passed[, <M> skipped] in <T>s``).

Usage:
    python scripts/check_readme_counts.py pytest-output.txt [--readme README.md]

Exit codes:
    0  all claims match the live run
    1  drift: at least one claim disagrees with the live run
    2  missing: no claims found in the README, or no summary in the output
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BADGE_RE = re.compile(r"tests-(\d+)%20passing")
BOLD_RE = re.compile(r"\*\*(\d+) passed, (\d+) skipped")
SUMMARY_RE = re.compile(r"(\d+) passed(?:, (\d+) skipped)? in [\d.]+s")


def parse_pytest_summary(text):
    """Return (passed, skipped) from the LAST pytest summary line, else None."""
    matches = SUMMARY_RE.findall(text)
    if not matches:
        return None
    passed, skipped = matches[-1]
    return int(passed), int(skipped) if skipped else 0


def find_claims(readme_text):
    """Return list of (label, passed, skipped_or_None) claims in the README."""
    claims = []
    for m in BADGE_RE.finditer(readme_text):
        claims.append(("badge 'tests-N passing'", int(m.group(1)), None))
    for m in BOLD_RE.finditer(readme_text):
        claims.append(
            ("inline '**N passed, M skipped**'", int(m.group(1)), int(m.group(2)))
        )
    return claims


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pytest_output", help="file containing pytest -q output")
    parser.add_argument(
        "--readme",
        default=str(REPO_ROOT / "README.md"),
        help="README to check (default: repo README.md)",
    )
    args = parser.parse_args(argv)

    output_text = Path(args.pytest_output).read_text(encoding="utf-8")
    readme_text = Path(args.readme).read_text(encoding="utf-8")

    actual = parse_pytest_summary(output_text)
    if actual is None:
        print("MISSING: no pytest summary line found in %s" % args.pytest_output)
        return 2
    actual_passed, actual_skipped = actual

    claims = find_claims(readme_text)
    if not claims:
        print("MISSING: no test-count claims found in %s" % args.readme)
        return 2

    print(
        "live run: %d passed, %d skipped -- checking %d README claim(s)"
        % (actual_passed, actual_skipped, len(claims))
    )
    drift = False
    for label, claimed_passed, claimed_skipped in claims:
        problems = []
        if claimed_passed != actual_passed:
            problems.append(
                "passed %d != live %d" % (claimed_passed, actual_passed)
            )
        if claimed_skipped is not None and claimed_skipped != actual_skipped:
            problems.append(
                "skipped %d != live %d" % (claimed_skipped, actual_skipped)
            )
        if problems:
            drift = True
            print("DRIFT: %s -- %s" % (label, "; ".join(problems)))
        else:
            print("OK: %s" % label)

    if drift:
        print("FAIL: README count claims have drifted from the live suite.")
        return 1
    print("OK: all README count claims match the live suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
