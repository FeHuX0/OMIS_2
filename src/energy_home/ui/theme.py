STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 14px; color: #203b38; }
QMainWindow, #page, QScrollArea { background: #f3f6f3; }
QScrollArea { border: none; }
QFrame#header { background: white; border-bottom: 1px solid #dce5df; }
QFrame#card { background: white; border: 1px solid #dce5df; border-radius: 12px; }
QLabel#brand { color: #147b63; font-size: 23px; font-weight: 700; }
QLabel#title { font-size: 27px; font-weight: 650; }
QLabel#subtitle { color: #72867f; font-size: 13px; }
QLabel#metric { font-size: 30px; font-weight: 650; color: #147b63; }
QLabel#section { font-size: 17px; font-weight: 650; }
QLabel#error { color: #b64634; }
QLabel#badge { background: #e7f4ec; color: #147b63; padding: 6px 12px; border-radius: 6px; }
QPushButton { background: white; border: 1px solid #cbd9d0; border-radius: 7px;
              padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #eaf3ed; border-color: #74ab94; }
QPushButton:pressed { background: #d4e8dc; }
QPushButton#primary { background: #187b63; border-color: #187b63; color: white; }
QPushButton#primary:hover { background: #12654f; }
QPushButton:disabled { color: #9baaa2; background: #edf1ed; border-color: #dce5df; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {
    background: white; border: 1px solid #cbd9d0; border-radius: 6px; padding: 8px;
    selection-background-color: #187b63; min-height: 20px;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #187b63; }
QTreeWidget, QTableWidget, QListWidget, QPlainTextEdit {
    background: white; border: 1px solid #dce5df; border-radius: 7px; outline: none;
    alternate-background-color: #f6f9f6; selection-background-color: #d6eee1;
    selection-color: #164c3b;
}
QTreeWidget::item { padding: 10px 4px; }
QListWidget::item { padding: 12px 8px; border-bottom: 1px solid #edf1ed; }
QHeaderView::section { background: #eaf1eb; border: none; padding: 9px; font-weight: 600; }
QTabWidget::pane { border: 1px solid #dce5df; background: white; border-radius: 7px; }
QTabBar::tab { padding: 11px 16px; background: #e6ede7; border: none; }
QTabBar::tab:selected { background: white; color: #147b63; }
QProgressBar { border: none; background: #e5eee7; border-radius: 5px; min-height: 12px; }
QProgressBar::chunk { background: #65aa82; border-radius: 5px; }
QCheckBox { spacing: 9px; padding: 4px 0; }
QDialog { background: #f3f6f3; }
QToolTip { background: #203b38; color: white; border: none; padding: 5px; }
"""


def configure_application(application):
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    # Offscreen-плагин Qt в Windows не перечисляет системные шрифты самостоятельно.
    if not QFontDatabase.families():
        for filename in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
            font = Path("C:/Windows/Fonts") / filename
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
