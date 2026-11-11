from pathlib import Path

import pytest

from energy_home.qt_runtime import prepare_qt_environment

prepare_qt_environment()

pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from energy_home.domain import AccessDenied, Screen  # noqa: E402
from energy_home.ui.theme import configure_application  # noqa: E402
from energy_home.ui.window import AppWindow  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    yield app


def test_all_five_screens_and_family_access(qt_app, system, tmp_path):
    _, _, energy, reports, _, _, _, _ = system
    window = AppWindow(energy, reports)
    window.show()
    window.timer.stop()
    window.login_name.setText("owner")
    window.login_password.setText("testpassword")
    QTest.keyClick(window.login_password, Qt.Key.Key_Return)
    qt_app.processEvents()
    assert window.navigation.current == Screen.HOME
    for screen in (Screen.DEVICES, Screen.REPORTS, Screen.SETTINGS, Screen.HOME):
        window.navigate(screen)
        qt_app.processEvents()
        assert window.stack.currentWidget() is window.pages[screen]
        if screen == Screen.SETTINGS:
            assert window.budget_input.lineEdit().width() > 20
            assert window.budget_input.text()
    assert window.grab().save(str(Path(tmp_path) / "home.png"))
    window.logout()
    assert window.navigation.current == Screen.LOGIN
    window.login_name.setText("family")
    window.login_password.setText("testpassword")
    window.login()
    window.navigate(Screen.DEVICES)
    assert not window.device_owner_buttons[0].isVisible()
    window.navigate(Screen.REPORTS)
    with pytest.raises(AccessDenied):
        window.navigate(Screen.SETTINGS)
    window.close()


def test_pdf_writer_produces_report(qt_app, system, tmp_path):
    from PySide6.QtGui import QPdfWriter, QTextDocument

    _, _, _, reports, owner, *_ = system
    report = reports.build(owner)
    target = tmp_path / "report.pdf"
    writer = QPdfWriter(str(target))
    doc = QTextDocument()
    doc.setHtml(reports.html(owner, report))
    doc.print_(writer)
    del writer
    assert target.read_bytes().startswith(b"%PDF-")
    assert target.stat().st_size > 1000
