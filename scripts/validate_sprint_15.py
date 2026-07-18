"""Orion AI Sprint 1.5 Validation Runner.

Usage:
    python scripts/validate_sprint_15.py

This script:
1. Checks environment (FFmpeg, Python version)
2. Creates synthetic test videos if no real samples exist
3. Runs regression tests
4. Runs benchmark suite on available videos
5. Generates summary report
"""

import asyncio
import subprocess
import sys
from pathlib import Path


def check_environment():
    """Verify required tools are installed."""
    print("Checking environment...")
    
    # Python version
    if sys.version_info < (3, 11):
        print("ERROR: Python 3.11+ required")
        sys.exit(1)
    print(f"  Python: {sys.version.split()[0]} OK")

    # FFmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        if result.returncode == 0:
            version = result.stdout.split("\n")[0].split()[2]
            print(f"  FFmpeg: {version} OK")
        else:
            raise RuntimeError()
    except Exception:
        print("  FFmpeg: NOT FOUND - Please install FFmpeg")
        sys.exit(1)

    # Backend dependencies
    try:
        import fastapi, uvicorn, pydantic, sqlalchemy, cv2, numpy, librosa, networkx
        print("  Python deps: OK")
    except ImportError as e:
        print(f"  Python deps: MISSING - {e}")
        print("  Run: pip install -r backend/requirements/dev.txt")
        sys.exit(1)


def create_synthetic_samples():
    """Create synthetic test videos if no real samples exist."""
    from backend.src.infrastructure.config.settings import settings
    samples_dir = settings.ORION_HOME / "benchmark_samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    categories = [
        ("gaming_test", "gaming"),
        ("podcast_test", "podcast"),
        ("tutorial_test", "tutorial"),
        ("sports_test", "sports"),
        ("music_test", "music"),
        ("cinematic_test", "cinematic"),
    ]

    created = 0
    for name, category in categories:
        path = samples_dir / f"{name}.mp4"
        if path.exists():
            continue

        print(f"Creating synthetic {category} video: {path.name}")
        # Generate a 20-second test video with some variation
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=duration=20:size=1920x1080:rate=30",
            "-f", "lavfi",
            "-i", f"sine=frequency=1000:duration=20",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", "fast",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            created += 1
        else:
            print(f"  Warning: Failed to create {path.name}: {result.stderr[:200]}")

    return created


def run_regression_tests():
    """Run pytest regression suite."""
    print("\nRunning regression tests...")
    test_file = Path(__file__).parent.parent / "backend" / "tests" / "integration" / "test_sprint_15_regression.py"
    if not test_file.exists():
        print(f"  Test file not found: {test_file}")
        return False

    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-v", "--tb=short"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print("REGRESSION TESTS FAILED")
        print(result.stderr)
        return False
    print("REGRESSION TESTS PASSED")
    return True


def run_benchmark():
    """Run benchmark suite."""
    print("\nRunning benchmark suite...")
    runner_path = Path(__file__).parent.parent / "backend" / "src" / "infrastructure" / "benchmark" / "benchmark_runner.py"
    if not runner_path.exists():
        print(f"  Benchmark runner not found: {runner_path}")
        return False

    result = subprocess.run(
        [sys.executable, str(runner_path)],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)
    return result.returncode == 0


def main():
    print("="*60)
    print("ORION AI SPRINT 1.5 VALIDATION")
    print("="*60)

    check_environment()
    created = create_synthetic_samples()
    if created > 0:
        print(f"\nCreated {created} synthetic sample videos")
    
    reg_ok = run_regression_tests()
    bench_ok = run_benchmark()

    print("\n" + "="*60)
    print("VALIDATION SUMMARY")
    print("="*60)
    print(f"Regression Tests: {'PASSED' if reg_ok else 'FAILED'}")
    print(f"Benchmark Suite:  {'PASSED' if bench_ok else 'FAILED'}")

    if reg_ok and bench_ok:
        print("\nSprint 1.5 VALIDATED. Ready for Sprint 2.")
        return 0
    else:
        print("\nSprint 1.5 VALIDATION FAILED. Review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
