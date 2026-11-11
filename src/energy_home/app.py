import argparse
import logging
import sys
from pathlib import Path

from .auth import AccessControl
from .domain import DomainError
from .reports import ReportService
from .services import EnergyService
from .storage import Database


def main():
    parser = argparse.ArgumentParser(description="Energy Home — учёт энергопотребления дома")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/energy_home.sqlite3"),
        help="Путь к базе SQLite (по умолчанию data/energy_home.sqlite3)",
    )
    parser.add_argument("--demo", action="store_true", help="Добавить демонстрационный дом")
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=args.db.parent / "energy_home.log",
        encoding="utf-8",
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.theme import configure_application
    from .ui.window import AppWindow

    application = QApplication(sys.argv[:1])
    application.setApplicationName("Energy Home")
    application.setOrganizationName("Energy Home")
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Belarus))
    configure_application(application)
    db = Database(args.db)
    access = AccessControl(db)
    energy = EnergyService(db, access)
    reports = ReportService(db, access)

    def show_exception(exc_type, exc_value, traceback):
        logging.error("Необработанная ошибка", exc_info=(exc_type, exc_value, traceback))
        QMessageBox.critical(
            None,
            "Ошибка приложения",
            "Действие не выполнено. Подробности записаны в energy_home.log рядом с базой данных.",
        )

    sys.excepthook = show_exception
    try:
        if args.demo:
            from .demo import seed_demo

            seed_demo(energy)
        window = AppWindow(energy, reports, demo=args.demo)
        window.show()
        return application.exec()
    except DomainError as exc:
        QMessageBox.critical(None, "Не удалось запустить приложение", str(exc))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
