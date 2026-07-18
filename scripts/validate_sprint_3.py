"""Sprint 3 validation script.

Usage:
    python scripts/validate_sprint_3.py

Validates:
1. Viral Score Engine produces composite scores
2. Hook Optimizer selects strategies
3. Retention Simulator detects drops
4. Audience Model adapts per platform
5. Creative Director optimizes for viral
6. End-to-end pipeline with platform selection
"""

import subprocess
import sys


def main():
    print("="*60)
    print("ORION AI SPRINT 3 VALIDATION")
    print("="*60)

    # Run Sprint 3 tests
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_sprint_3.py", "-v", "--tb=short"],
        capture_output=True,
        text=True,
        cwd="backend",
    )
    print(result.stdout)
    if result.returncode != 0:
        print("SPRINT 3 TESTS FAILED")
        print(result.stderr)
        return 1

    print("\n" + "="*60)
    print("SPRINT 3 VALIDATED SUCCESSFULLY")
    print("="*60)
    print("Viral Intelligence layer is functional.")
    print("Ready for Sprint 4: Feedback Learning & Real-World Calibration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
