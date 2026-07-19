"""Install ORION into temporary venvs and execute both planning smoke profiles."""

import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path, quiet: bool = False) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=quiet,
        text=quiet,
    )
    if completed.returncode != 0:
        if quiet:
            print(completed.stdout)
            print(completed.stderr, file=sys.stderr)
        completed.check_returncode()


def venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def verify_profile(*, smoke_script: str) -> None:
    with tempfile.TemporaryDirectory(prefix="orion-install-smoke-") as directory:
        temporary = Path(directory)
        environment = temporary / "venv"
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
        python = venv_python(environment)
        metadata = temporary / "metadata"
        build = temporary / "build"
        metadata.mkdir()
        run(
            [
                str(python),
                "-c",
                "from setuptools import setup; setup()",
                "egg_info",
                "--egg-base",
                str(metadata),
                "build",
                "--build-base",
                str(build),
                "install",
                "--prefix",
                str(environment),
                "--single-version-externally-managed",
                "--record",
                str(temporary / "installed-files.txt"),
            ],
            cwd=ROOT,
            quiet=True,
        )
        run([str(python), str(ROOT / "scripts" / smoke_script)], cwd=temporary)


def main() -> None:
    verify_profile(smoke_script="smoke_planning_minimal.py")
    verify_profile(smoke_script="smoke_planning_openai.py")
    print("planning installation profiles: OK")


if __name__ == "__main__":
    main()
