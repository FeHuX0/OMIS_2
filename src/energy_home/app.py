import argparse
import logging
import os
import sys
from pathlib import Path

from .auth import AccessControl
from .domain import DomainError
from .qt_runtime import ensure_pyside6, prepare_qt_environment
from .reports import ReportService
from .services import EnergyService
from .storage import Database


def _run_qt(db_path: Path, *, demo: bool = False) -> int:
    prepare_qt_environment()
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui.theme import configure_application
    from .ui.window import AppWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("Energy Home")
    application.setOrganizationName("Energy Home")
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Belarus))
    configure_application(application)
    db = Database(db_path)
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
        if demo:
            from .demo import seed_demo

            seed_demo(energy)
        window = AppWindow(energy, reports, demo=demo)
        window.show()
        return application.exec()
    except DomainError as exc:
        QMessageBox.critical(None, "Не удалось запустить приложение", str(exc))
        return 1
    finally:
        db.close()


def _run_tk(db_path: Path, *, demo: bool = False) -> int:
    from .ui.tk_window import TkAppWindow

    db = Database(db_path)
    access = AccessControl(db)
    energy = EnergyService(db, access)
    reports = ReportService(db, access)
    try:
        if demo:
            from .demo import seed_demo

            seed_demo(energy)
        window = TkAppWindow(energy, reports, demo=demo)
        return window.run()
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Energy Home — учёт энергопотребления дома")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/energy_home.sqlite3"),
        help="Путь к базе SQLite (по умолчанию data/energy_home.sqlite3)",
    )
    parser.add_argument("--demo", action="store_true", help="Добавить демонстрационный дом")
    parser.add_argument("--web", action="store_true", help="Запустить веб-интерфейс")
    parser.add_argument("--port", type=int, default=0, help="Порт веб-сервера")
    args = parser.parse_args(argv)
    args.db.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=args.db.parent / "energy_home.log",
        encoding="utf-8",
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    env_port = int(os.environ.get("PORT", "0") or "0")
    if args.web or env_port > 0:
        from .ui.web import run_web_app

        port = args.port or env_port or 8765
        host = os.environ.get("HOST", "0.0.0.0" if env_port > 0 else "127.0.0.1")
        return run_web_app(
            args.db,
            demo=args.demo or env_port > 0,
            host=host,
            port=port,
            open_browser=env_port == 0,
        )

    qt_ok, qt_err = ensure_pyside6()
    if qt_ok:
        return _run_qt(args.db, demo=args.demo)

    logging.warning("Переключение на резервный интерфейс (PySide6 недоступен: %s)", qt_err)
    try:
        return _run_tk(args.db, demo=args.demo)
    except Exception as tk_exc:
        logging.warning("Переключение на локальный веб-интерфейс (%s)", tk_exc)
        from .ui.web import run_web_app

        return run_web_app(
            args.db,
            demo=args.demo,
            host="127.0.0.1",
            port=args.port or 8765,
            open_browser=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
