#!/usr/bin/env python3
"""Local pre-commit quality gate.

Runs the same checks as CI/CD locally so developers catch issues before pushing.

Usage:
    python scripts/lint_check.py           # Full check
    python scripts/lint_check.py --fix    # Auto-fix where possible
"""

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_cmd(cmd: list[str], label: str, fix: bool = False) -> int:
    """Run a command and print formatted output."""
    print(f"\n{'='*60}")
    print(f"Running: {label}")
    print(f"{'='*60}")
    if fix and cmd[0] in ("black", "ruff"):
        if cmd[0] == "black":
            cmd = cmd[:] + ["--"]
        elif cmd[0] == "ruff":
            cmd = ["ruff", "check", "--fix", *cmd[2:]]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"[{status}] {label}")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Orion AI Local Lint & Quality Gate")
    parser.add_argument("--fix", action="store_true", help="Auto-fix formatting issues")
    args = parser.parse_args()

    checks = [
        (["ruff", "check", "backend/src", "backend/tests", "scripts"], "Ruff Linter"),
        (["black", "--check", "backend/src", "backend/tests", "scripts"], "Black Format"),
        (["mypy", "backend/src", "--ignore-missing-imports"], "MyPy Type Check"),
    ]

    exit_codes = []
    for cmd, label in checks:
        exit_codes.append(run_cmd(cmd, label, fix=args.fix))

    print(f"\n{'='*60}")
    print("QUALITY GATE SUMMARY")
    print(f"{'='*60}")
    for (_, label), code in zip(checks, exit_codes):
        status = "PASS" if code == 0 else "FAIL"
        print(f"  [{status}] {label}")

    if any(exit_codes):
        print("\nSome checks failed. Fix before pushing.")
        if args.fix:
            print("(You used --fix; re-run without --fix to verify.)")
        return 1

    print("\nAll checks passed! Ready to push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
