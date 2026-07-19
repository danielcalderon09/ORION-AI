"""Smoke-check the installed minimal profile while explicitly blocking httpx."""

import importlib.abc
import logging
import os
import sys
import tempfile
from pathlib import Path


class OptionalHttpBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "httpx" or fullname.startswith("httpx."):
            raise ModuleNotFoundError(
                "httpx is intentionally unavailable in the minimal profile",
                name="httpx",
            )
        return None


def main() -> None:
    sys.meta_path.insert(0, OptionalHttpBlocker())
    with tempfile.TemporaryDirectory(prefix="orion-minimal-smoke-") as directory:
        root = Path(directory)
        os.environ.update(
            {
                "ORION_HOME": str(root / "home"),
                "MODELS_DIR": str(root / "models"),
                "PROJECTS_DIR": str(root / "projects"),
                "TEMP_DIR": str(root / "temp"),
                "ORION_PROMPT_VIDEO_ENABLED": "false",
            }
        )
        from backend.src.infrastructure.config.settings import Settings
        from backend.src.main import create_app
        from backend.src.production.planning.providers import SimulatedPlanningProvider

        settings = Settings(
            _env_file=None,
            ORION_HOME=root / "home",
            MODELS_DIR=root / "models",
            PROJECTS_DIR=root / "projects",
            TEMP_DIR=root / "temp",
            ORION_PROMPT_VIDEO_ENABLED=False,
        )
        app = create_app(settings)
        assert SimulatedPlanningProvider is not None
        assert app is not None
        assert "backend.src.production.planning.providers.openai_provider" not in sys.modules
        assert "httpx" not in sys.modules
        logging.shutdown()
    print("minimal planning installation smoke: OK")


if __name__ == "__main__":
    main()
