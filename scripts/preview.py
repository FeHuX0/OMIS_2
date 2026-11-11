"""Снимки пяти окон на временной БД: uv run python scripts/preview.py."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from energy_home.auth import AccessControl  # noqa: E402
from energy_home.demo import DEMO_PASSWORD, seed_demo  # noqa: E402
from energy_home.domain import Screen  # noqa: E402
from energy_home.reports import ReportService  # noqa: E402
from energy_home.services import EnergyService  # noqa: E402
from energy_home.storage import Database  # noqa: E402
from energy_home.ui.theme import configure_application  # noqa: E402
from energy_home.ui.window import AppWindow  # noqa: E402

app = QApplication([])
configure_application(app)
db = Database(":memory:")
access = AccessControl(db)
energy = EnergyService(db, access)
seed_demo(energy)
window = AppWindow(energy, ReportService(db, access), demo=True)
window.timer.stop()
window.show()
output = Path("docs/screenshots")
output.mkdir(parents=True, exist_ok=True)
app.processEvents()
window.grab().save(str(output / "01-login.png"))
window.login_name.setText("owner@demo.local")
window.login_password.setText(DEMO_PASSWORD)
window.login()
window.simulate.setChecked(True)
window.tick()
for name, screen in [
    ("02-home", Screen.HOME),
    ("03-devices", Screen.DEVICES),
    ("04-reports", Screen.REPORTS),
    ("05-settings", Screen.SETTINGS),
]:
    window.navigate(screen)
    app.processEvents()
    window.grab().save(str(output / f"{name}.png"))
window.close()
db.close()
print(f"Screenshots: {output.resolve()}")
