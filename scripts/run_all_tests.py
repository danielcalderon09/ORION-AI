#!/usr/bin/env python3
"""Unified test runner for Orion AI CI/CD.

Executes the full test suite in the correct order:
1. Unit tests
2. Integration tests (excluding FFmpeg-dependent ones)
3. Golden dataset regression
4. Stress tests

Produces a combined JSON report with coverage, timings, and pass/fail status.

Usage:
    python scripts/run_all_tests.py [--coverage] [--golden] [--stress] [--output=report.json]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = PROJECT_ROOT / "backend" / "tests"


def run_pytest(path: Path, label: str, coverage: bool = False, extra_args: list[str] | None = None) -> dict[str, Any]:
    """Run pytest on a test directory and capture results."""
    cmd = [
        sys.executable, "-m", "pytest",
        str(path),
        "-v",
        "--tb=short",
    ]
    if coverage:
        cmd += [
            "--cov=backend/src",
            "--cov-append",
            "--cov-report=term-missing",
        ]
    if extra_args:
        cmd += extra_args

    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    duration = time.time() - start

    # Parse basic stats from stdout
    passed = result.stdout.count(" PASSED")
    failed = result.stdout.count(" FAILED")
    error = result.stdout.count(" ERROR")
    skipped = result.stdout.count(" SKIPPED")

    return {
        "label": label,
        "path": str(path.relative_to(PROJECT_ROOT)),
        "returncode": result.returncode,
        "duration_seconds": round(duration, 2),
        "passed": passed,
        "failed": failed,
        "error": error,
        "skipped": skipped,
        "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
        "stderr": result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Orion AI Unified Test Runner")
    parser.add_argument("--coverage", action="store_true", help="Enable coverage reporting")
    parser.add_argument("--golden", action="store_true", help="Run golden dataset tests")
    parser.add_argument("--stress", action="store_true", help="Run stress tests")
    parser.add_argument("--output", default="test-report.json", help="Output report path")
    parser.add_argument("--quick", action="store_true", help="Skip golden/stress, run only unit+integration")
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    overall_start = time.time()

    # 1. Unit tests
    print("=" * 60)
    print("Running UNIT TESTS...")
    print("=" * 60)
    unit_result = run_pytest(TEST_DIR / "unit", "unit", coverage=args.coverage)
    results.append(unit_result)
    print(unit_result["stdout"])
    if unit_result["stderr"]:
        print(unit_result["stderr"], file=sys.stderr)

    # 2. Integration tests (skip FFmpeg-dependent Sprint 1.5 tests)
    print("\n" + "=" * 60)
    print("Running INTEGRATION TESTS...")
    print("=" * 60)
    int_result = run_pytest(
        TEST_DIR / "integration",
        "integration",
        coverage=args.coverage,
        extra_args=["--ignore=backend/tests/integration/test_sprint_15_regression.py"],
    )
    results.append(int_result)
    print(int_result["stdout"])
    if int_result["stderr"]:
        print(int_result["stderr"], file=sys.stderr)

    # 3. Golden dataset (optional)
    if args.golden and not args.quick:
        print("\n" + "=" * 60)
        print("Running GOLDEN DATASET TESTS...")
        print("=" * 60)
        golden_result = run_pytest(TEST_DIR / "golden", "golden", coverage=args.coverage)
        results.append(golden_result)
        print(golden_result["stdout"])

    # 4. Stress tests (optional)
    if args.stress and not args.quick:
        print("\n" + "=" * 60)
        print("Running STRESS TESTS...")
        print("=" * 60)
        stress_result = run_pytest(TEST_DIR / "stress", "stress", coverage=args.coverage)
        results.append(stress_result)
        print(stress_result["stdout"])

    overall_duration = time.time() - overall_start

    # Generate report
    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)
    total_error = sum(r["error"] for r in results)
    total_skipped = sum(r["skipped"] for r in results)

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overall_duration_seconds": round(overall_duration, 2),
        "summary": {
            "total_passed": total_passed,
            "total_failed": total_failed,
            "total_error": total_error,
            "total_skipped": total_skipped,
            "total_tests": total_passed + total_failed + total_error + total_skipped,
            "success": all(r["returncode"] == 0 for r in results),
        },
        "stages": results,
    }

    output_path = PROJECT_ROOT / args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"Passed:   {total_passed}")
    print(f"Failed:   {total_failed}")
    print(f"Errors:   {total_error}")
    print(f"Skipped:  {total_skipped}")
    print(f"Duration: {overall_duration:.1f}s")
    print(f"Report:   {output_path}")

    return 0 if report["summary"]["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
