"""Setup script for development environment."""

import subprocess
import sys
from pathlib import Path


def check_python_version():
    if sys.version_info < (3, 11):
        print("ERROR: Python 3.11+ required")
        sys.exit(1)
    print(f"Python version: {sys.version}")


def install_dependencies():
    print("Installing backend dependencies...")
    base_req = Path(__file__).parent.parent / "backend" / "requirements" / "dev.txt"
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(base_req)], check=True)


def install_frontend_deps():
    print("Installing frontend dependencies...")
    frontend_dir = Path(__file__).parent.parent / "frontend"
    subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)


def create_directories():
    from backend.src.infrastructure.config.settings import Settings
    settings = Settings()
    print(f"Created Orion home at: {settings.ORION_HOME}")


def main():
    print("=== Orion AI Development Setup ===")
    check_python_version()
    install_dependencies()
    try:
        install_frontend_deps()
    except Exception as e:
        print(f"Frontend install skipped or failed: {e}")
    create_directories()
    print("Setup complete!")


if __name__ == "__main__":
    main()
