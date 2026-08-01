from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402 - platform must be configured first
from PySide6.QtWidgets import QApplication  # noqa: E402 - platform must be configured first


@pytest.fixture(scope="session")
def qt_application() -> Iterator[QApplication]:
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def qt_preferences(tmp_path) -> QSettings:
    settings = QSettings(str(tmp_path / "desktop-ui.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings
