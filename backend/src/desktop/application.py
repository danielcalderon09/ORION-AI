"""Desktop application bootstrap."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from backend.src.desktop.backend_client import ProductionDesktopBackend
from backend.src.desktop.main_window import OrionMainWindow


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("ORION AI")
    app.setOrganizationName("ORION")
    app.setStyle("Fusion")
    window = OrionMainWindow(backend=ProductionDesktopBackend())
    window.show()
    return app.exec()


__all__ = ["main"]
