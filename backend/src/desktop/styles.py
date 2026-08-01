"""Minimal dark visual system for the ORION desktop MVP."""

ORION_DARK_STYLESHEET = """
QWidget {
    background: #0b0d10;
    color: #e6e9ef;
    font-family: "Segoe UI";
    font-size: 13px;
}
QLabel { background: transparent; }
QMainWindow { background: #0b0d10; }
QFrame#panel, QFrame#card {
    background: #111419;
    border: 1px solid #222731;
    border-radius: 10px;
}
QLabel#appTitle { font-size: 24px; font-weight: 700; color: #f5f7fb; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; color: #f0f2f6; }
QLabel#muted { color: #858d9b; }
QLabel#errorBanner {
    background: #2a1719;
    border: 1px solid #693039;
    border-radius: 7px;
    color: #ffb4bd;
    padding: 10px;
}
QTextEdit, QComboBox, QLineEdit {
    background: #0d1014;
    border: 1px solid #2b313c;
    border-radius: 7px;
    padding: 9px;
    selection-background-color: #4666e5;
}
QTextEdit:focus, QComboBox:focus, QLineEdit:focus { border-color: #607eea; }
QComboBox::drop-down { border: 0; width: 28px; }
QPushButton {
    background: #252a33;
    border: 1px solid #343b48;
    border-radius: 7px;
    color: #eef1f6;
    padding: 9px 14px;
    font-weight: 600;
}
QPushButton:hover { background: #303744; }
QPushButton:pressed { background: #20252d; }
QPushButton:disabled { color: #626a77; background: #171a20; border-color: #242932; }
QPushButton#primaryButton { background: #526ee8; border-color: #6982ef; color: white; }
QPushButton#primaryButton:hover { background: #627df0; }
QTreeWidget {
    background: transparent;
    border: 0;
    outline: 0;
    alternate-background-color: #15191f;
}
QTreeWidget::item { padding: 7px 4px; border-bottom: 1px solid #1d222a; }
QTreeWidget::item:selected { background: #252e47; color: white; }
QHeaderView::section {
    background: #111419;
    color: #7f8794;
    border: 0;
    border-bottom: 1px solid #242a33;
    padding: 7px 4px;
    font-size: 11px;
    font-weight: 600;
}
QProgressBar {
    background: #171b21;
    border: 0;
    border-radius: 4px;
    height: 7px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: #5b76eb; border-radius: 4px; }
QLabel[stageState="pending"] { color: #616a77; }
QLabel[stageState="active"] { color: #91a7ff; }
QLabel[stageState="complete"] { color: #6fd1a7; }
QLabel[stageState="failed"] { color: #ff8997; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { width: 8px; background: transparent; }
QScrollBar::handle:vertical { background: #303640; border-radius: 4px; min-height: 30px; }
QToolTip { background: #171b21; color: #eef1f6; border: 1px solid #343b48; }
"""

__all__ = ["ORION_DARK_STYLESHEET"]
