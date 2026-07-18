"""Sprint 4 validation script.

Usage:
    python scripts/validate_sprint_4.py

Validates the Auto-Improvement layer:
1. Reflection Engine proposes improvements
2. Critic AI evaluates independently
3. Multi Candidate Generator produces variants
4. Consensus Engine selects winners
5. Creative Memory stores patterns
6. Human Feedback learns from ratings
7. End-to-end orchestrator
"""

import subprocess
import sys


def main():
    print("="*60)
    print("ORION AI SPRINT 4 VALIDATION")
    print("Auto-Improvement & Continuous Learning")
    print("="*60)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_sprint_4.py", "-v", "--tb=short"],
        capture_output=True,
        text=True,
        cwd="backend",
    )
    print(result.stdout)
    if result.returncode != 0:
        print("SPRINT 4 TESTS FAILED")
        print(result.stderr)
        return 1

    print("\n" + "="*60)
    print("SPRINT 4 VALIDATED SUCCESSFULLY")
    print("="*60)
    print("Orion AI is now self-evaluating and self-correcting.")
    print("Ready for production hardening and real-world calibration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
