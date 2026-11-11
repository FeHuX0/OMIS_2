from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QPdfWriter, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..automation import AutomationService
from ..demo import DEMO_PASSWORD, seed_demo
from ..domain import (
    LOCAL_TZ,
    STATE_LABELS,
    TRANSITIONS,
    AccessDenied,
    DomainError,
    Navigation,
    ReadingPacket,
    Role,
    Screen,
    ValidationError,
    now,
)
from ..extensions import BuildingService, HomeAssistantAdapter, VoiceAssistant
from ..gateways import SensorGateway, Simulator, import_csv
from .widgets import (
    EnergyChart,
    button,
    card,
    combo,
    fill_table,
    form_dialog,
    integer,
    label,
    line,
    scroll,
    spin,
    table,
)


class AppWindow(QMainWindow):
    def __init__(self, energy, reports, *, demo=False):
        super().__init__()
        self.energy, self.reports, self.access = energy, reports, energy.access
        self.automation = AutomationService(energy)
        self.building = BuildingService(energy.db, self.access)
        self.assistant = VoiceAssistant(energy, reports, self.automation)
        self.gateway = SensorGateway(energy)
        self.simulator = Simulator(energy, self.gateway)
        self.navigation = Navigation()
        self.session = None
        self.user = None
        self.current_report = None
        self.network_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="home-assistant")
        self.ha_future = None
        self.ha_poll = QTimer(self)
        self.ha_poll.setInterval(100)
        self.ha_poll.timeout.connect(self.poll_home_assistant)
        self.setWindowTitle("Energy Home — учёт энергопотребления")
        self.resize(1240, 900)
        self.setMinimumSize(1000, 720)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = self.make_header()
        layout.addWidget(self.header)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.pages = {}
        self.make_login()
        self.make_home()
        self.make_devices()
        self.make_reports()
        self.make_settings()
        self.setCentralWidget(root)
        self.header.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(10_000)
        self.timer.timeout.connect(lambda: self.guard(self.tick))
        self.timer.start()
        if demo:
            self.login_name.setText("owner@demo.local")
            self.login_password.setText(DEMO_PASSWORD)
            self.demo_hint.setText(f"Демо: owner@demo.local / {DEMO_PASSWORD}")

    def guard(self, action):
        try:
            return action()
        except AccessDenied as exc:
            QMessageBox.warning(self, "Доступ", str(exc))
            if self.session:
                try:
                    self.access.require(self.session)
                except AccessDenied:
                    self.logout()
        except (DomainError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Не удалось выполнить действие", str(exc))

    def page(self, screen, title, subtitle):
        widget = QWidget()
        widget.setObjectName("page")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "subtitle"))
        self.pages[screen] = scroll(widget)
        self.stack.addWidget(self.pages[screen])
        return layout

    def make_header(self):
        frame = QFrame()
        frame.setObjectName("header")
        row = QHBoxLayout(frame)
        row.setContentsMargins(30, 16, 30, 16)
        row.addWidget(label("ϟ  Energy Home", "brand"))
        row.addSpacing(30)
        self.nav_buttons = {}
        for text, screen in [
            ("Обзор", Screen.HOME),
            ("Устройства", Screen.DEVICES),
            ("Отчёты", Screen.REPORTS),
            ("Настройки", Screen.SETTINGS),
        ]:
            b = button(text, lambda target=screen: self.guard(lambda: self.navigate(target)))
            self.nav_buttons[screen] = b
            row.addWidget(b)
        row.addStretch()
        self.profile_label = label("")
        row.addWidget(self.profile_label)
        row.addWidget(button("Выйти", self.logout))
        return frame

    def make_login(self):
        widget = QWidget()
        widget.setObjectName("page")
        outer = QVBoxLayout(widget)
        outer.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        panel, layout = card()
        panel.setFixedWidth(470)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.addWidget(label("ϟ  Energy Home", "brand"))
        layout.addSpacing(12)
        layout.addWidget(label("Энергия под контролем", "title"))
        layout.addWidget(label("Потребление, расходы и забота о вашем доме.", "subtitle"))
        layout.addSpacing(16)
        layout.addWidget(label("Логин / E-mail"))
        self.login_name = line("Ваш логин")
        self.login_name.setObjectName("login_name")
        layout.addWidget(self.login_name)
        layout.addWidget(label("Пароль"))
        self.login_password = line("Не менее 8 символов", password=True)
        self.login_password.setObjectName("login_password")
        self.login_password.returnPressed.connect(self.login)
        layout.addWidget(self.login_password)
        self.login_error = label("", "error")
        layout.addWidget(self.login_error)
        layout.addWidget(button("Войти", self.login, True))
        layout.addWidget(button("Зарегистрировать свой дом", lambda: self.guard(self.register)))
        layout.addSpacing(8)
        layout.addWidget(button("Открыть демонстрационный дом", lambda: self.guard(self.open_demo)))
        self.demo_hint = label("Данные хранятся локально на вашем компьютере.", "subtitle")
        layout.addWidget(self.demo_hint)
        row.addWidget(panel)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()
        self.pages[Screen.LOGIN] = scroll(widget)
        self.stack.addWidget(self.pages[Screen.LOGIN])

    def login(self):
        try:
            session = self.access.login(self.login_name.text(), self.login_password.text())
            self.finish_login(session)
            self.login_error.clear()
        except DomainError as exc:
            self.login_error.setText(str(exc))

    def finish_login(self, session):
        self.session = session
        self.user = self.access.require(session)
        role = "Владелец" if self.user["role"] == Role.OWNER else "Член семьи"
        self.profile_label.setText(f"{self.user['name']} · {role}")
        self.nav_buttons[Screen.SETTINGS].setVisible(self.user["role"] == Role.OWNER)
        self.simulate.setChecked(False)
        self.simulate.setVisible(self.user["role"] == Role.OWNER)
        self.header.show()
        self.navigate(Screen.HOME)

    def register(self):
        name, login, password, home, address = line(), line(), line(password=True), line(), line()
        kind = combo([("Квартира", "apartment"), ("Частный дом", "house")])
        if form_dialog(
            self,
            "Регистрация дома",
            [
                ("Ваше имя", name),
                ("Логин / E-mail", login),
                ("Пароль", password),
                ("Название дома", home),
                ("Тип жилья", kind),
                ("Адрес", address),
            ],
            accept="Создать дом",
        ):
            session = self.access.register_home(
                name.text(),
                login.text(),
                password.text(),
                home.text(),
                address.text(),
                kind.currentData(),
            )
            self.finish_login(session)

    def open_demo(self):
        seed_demo(self.energy)
        self.login_name.setText("owner@demo.local")
        self.login_password.setText(DEMO_PASSWORD)
        self.login()
        if self.session:
            self.simulate.setChecked(True)
            self.tick()

    def logout(self):
        if self.session:
            self.access.logout(self.session)
        self.session = self.user = None
        self.simulate.setChecked(False)
        self.login_password.clear()
        self.ha_token.clear()
        self.ha_result.clear()
        self.ha_poll.stop()
        if self.ha_future:
            self.ha_future.cancel()
            self.ha_future = None
        self.ha_read_button.setEnabled(True)
        self.navigation.go(Screen.LOGIN) if self.navigation.current != Screen.LOGIN else None
        self.header.hide()
        self.stack.setCurrentWidget(self.pages[Screen.LOGIN])

    def navigate(self, screen):
        self.user = self.access.require(self.session, owner=screen == Screen.SETTINGS)
        if screen == self.navigation.current:
            self.refresh(screen)
            return
        if self.navigation.current not in (Screen.LOGIN, Screen.HOME) and screen != Screen.HOME:
            self.navigation.go(Screen.HOME, Role(self.user["role"]))
        self.navigation.go(screen, Role(self.user["role"]))
        self.refresh(screen)
        self.stack.setCurrentWidget(self.pages[screen])
        for key, b in self.nav_buttons.items():
            b.setStyleSheet("color:#147b63; background:#e3f1e7;" if key == screen else "")

    def refresh(self, screen):
        {
            Screen.HOME: self.refresh_home,
            Screen.DEVICES: self.refresh_devices,
            Screen.REPORTS: self.refresh_reports,
            Screen.SETTINGS: self.refresh_settings,
        }[screen]()

    def make_home(self):
        layout = self.page(Screen.HOME, "Ваш дом сегодня", "Всё, что нужно знать об энергии дома.")
        top = QHBoxLayout()
        self.home_name = label("", "section")
        top.addWidget(self.home_name)
        top.addStretch()
        self.home_room = combo([("Все комнаты", None)])
        self.home_device = combo([("Все приборы", None)])
        top.addWidget(self.home_room)
        top.addWidget(self.home_device)
        self.home_room.currentIndexChanged.connect(lambda: self.guard(self.refresh_home))
        self.home_device.currentIndexChanged.connect(lambda: self.guard(self.refresh_home))
        self.simulate = QCheckBox("Симулятор")
        self.simulate.setToolTip("Генерировать локальные показания каждые 10 секунд")
        top.addWidget(self.simulate)
        layout.addLayout(top)
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, title, hint in [
            ("power", "Текущая мощность", "По устройствам на связи"),
            ("daily", "Расход за сегодня", "С полуночи · Минск"),
            ("cost", "Расходы за месяц", "С учётом тарифных зон"),
        ]:
            frame, column = card()
            column.addWidget(label(title, "subtitle"))
            self.metrics[key] = label("—", "metric")
            column.addWidget(self.metrics[key])
            column.addWidget(label(hint, "subtitle"))
            metrics.addWidget(frame)
        layout.addLayout(metrics)
        self.budget_label = label("")
        layout.addWidget(self.budget_label)
        self.budget_bar = QProgressBar()
        self.budget_bar.setTextVisible(False)
        layout.addWidget(self.budget_bar)
        middle = QHBoxLayout()
        self.home_chart = EnergyChart()
        middle.addWidget(self.home_chart, 3)
        panel, column = card()
        column.addWidget(label("Уведомления", "section"))
        self.notification_list = QListWidget()
        self.notification_list.setWordWrap(True)
        self.notification_list.setMinimumHeight(200)
        column.addWidget(self.notification_list)
        column.addWidget(button("Отметить прочитанными", lambda: self.guard(self.mark_read)))
        middle.addWidget(panel, 2)
        layout.addLayout(middle)
        self.connection_label = label("", "subtitle")
        layout.addWidget(self.connection_label)
        actions = QHBoxLayout()
        actions.addWidget(
            button(
                "Устройства и приборы  →", lambda: self.guard(lambda: self.navigate(Screen.DEVICES))
            )
        )
        actions.addWidget(
            button(
                "Посмотреть статистику  →",
                lambda: self.guard(lambda: self.navigate(Screen.REPORTS)),
            )
        )
        self.home_settings = button(
            "Настройки владельца  →", lambda: self.guard(lambda: self.navigate(Screen.SETTINGS))
        )
        actions.addWidget(self.home_settings)
        actions.addWidget(button("Помощник", lambda: self.guard(self.voice_dialog)))
        layout.addLayout(actions)
        layout.addStretch()

    @staticmethod
    def refill_combo(widget, items):
        previous = widget.currentData()
        widget.blockSignals(True)
        widget.clear()
        for title, data in items:
            widget.addItem(title, data)
        index = widget.findData(previous)
        widget.setCurrentIndex(max(0, index))
        widget.blockSignals(False)

    def refresh_home(self):
        if not self.session:
            return
        self.user = self.access.require(self.session)
        self.home_name.setText(self.energy.home(self.session)["name"])
        self.home_settings.setVisible(self.user["role"] == Role.OWNER)
        devices = self.energy.devices(self.session)
        self.refill_combo(
            self.home_room,
            [("Все комнаты", None)]
            + [(r["name"], r["id"]) for r in self.energy.rooms(self.session)],
        )
        room_id = self.home_room.currentData()
        self.refill_combo(
            self.home_device,
            [("Все приборы", None)]
            + [(d["name"], d["id"]) for d in devices if room_id is None or d["room_id"] == room_id],
        )
        device_id = self.home_device.currentData()
        visible = [
            d
            for d in devices
            if (room_id is None or d["room_id"] == room_id)
            and (device_id is None or d["id"] == device_id)
        ]
        dashboard = self.reports.dashboard(self.session, room_id, device_id)
        power = sum(d["power_w"] for d in visible if d["counted"])
        self.metrics["power"].setText(f"{power / 1000:.2f} кВт")
        self.metrics["daily"].setText(f"{dashboard['daily_kwh']:.2f} кВт·ч")
        self.metrics["cost"].setText(f"{dashboard['monthly_cost']:.2f} BYN")
        settings = self.energy.settings(self.session, owner=False)
        # Лимит относится ко всему дому и не меняется при фильтрации приборов.
        full = self.reports.dashboard(self.session)
        percent = full["monthly_cost"] / settings["monthly_budget"] * 100
        self.budget_label.setText(
            f"Бюджет дома: {full['monthly_cost']:.2f} из "
            f"{settings['monthly_budget']:.2f} BYN · {percent:.0f}%"
        )
        self.budget_bar.setValue(min(100, int(percent)))
        week = self.reports.build(self.session, "week", room_id=room_id, device_id=device_id)
        self.home_chart.update_data(week.series, "Расход на этой неделе · кВт·ч")
        self.notification_list.clear()
        for n in self.energy.notifications.list(self.session, 30):
            date = datetime.fromisoformat(n["created_at"]).astimezone(LOCAL_TZ)
            item = QListWidgetItem(
                f"{'● ' if not n['is_read'] else ''}{date:%d.%m %H:%M}\n" + n["message"]
            )
            if not n["is_read"]:
                item.setForeground(QColor("#12654f"))
            self.notification_list.addItem(item)
        if not self.notification_list.count():
            self.notification_list.addItem("Новых событий пока нет. Всё спокойно.")
        online = sum(d["online"] for d in devices)
        self.connection_label.setText(
            f"На связи: {online} из {len(devices)} устройств · "
            f"Обновлено {now():%H:%M:%S} · "
            + (
                "Источник: локальный симулятор"
                if self.simulate.isChecked()
                else "Показания: импорт CSV / ручной ввод"
            )
        )

    def mark_read(self):
        self.energy.notifications.mark_read(self.session)
        self.refresh_home()

    def tick(self):
        if not self.session:
            return
        self.user = self.access.require(self.session)
        if self.user["role"] == Role.OWNER:
            self.automation.tick(self.session)
            if self.simulate.isChecked():
                self.simulator.tick(self.session)
            _, errors = self.gateway.drain()
            if errors:
                self.statusBar().showMessage(errors[0], 10_000)
        if self.navigation.current == Screen.HOME:
            self.refresh_home()
        elif self.navigation.current == Screen.DEVICES:
            self.refresh_devices()

    def make_devices(self):
        layout = self.page(
            Screen.DEVICES,
            "Устройства и помещения",
            "Дерево комнат и приборов, состояние и поступление показаний.",
        )
        toolbar = QHBoxLayout()
        self.device_owner_buttons = []
        for text, action in [
            ("+ Добавить помещение", self.add_room),
            ("+ Добавить устройство", self.add_device),
            ("Импорт CSV", self.import_readings),
            ("Журнал событий", self.event_log),
        ]:
            b = button(text, lambda fn=action: self.guard(fn))
            self.device_owner_buttons.append(b)
            toolbar.addWidget(b)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["Комната / прибор", "Устройство учёта", "Состояние", "Мощность", "Последние данные"]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(340)
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self.tree)
        self.device_detail = label("Выберите прибор в дереве.", "subtitle")
        layout.addWidget(self.device_detail)
        row = QHBoxLayout()
        self.device_action = combo([])
        self.apply_device_action = button("Применить", lambda: self.guard(self.apply_state))
        self.manual_reading = button("Добавить показание", lambda: self.guard(self.add_reading))
        self.inject_anomaly = button("Проверить защиту", lambda: self.guard(self.test_anomaly))
        row.addWidget(self.device_action, 1)
        row.addWidget(self.apply_device_action)
        row.addWidget(self.manual_reading)
        row.addWidget(self.inject_anomaly)
        layout.addLayout(row)
        layout.addWidget(
            label(
                "При отсутствии показаний более 5 минут устройство считается "
                "не на связи. Авария снимается через сброс, затем отдельное включение.",
                "subtitle",
            )
        )
        layout.addWidget(
            button("← Назад в главное меню", lambda: self.guard(lambda: self.navigate(Screen.HOME)))
        )

    def selected_device(self):
        item = self.tree.currentItem()
        device_id = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not device_id:
            raise ValidationError("Выберите прибор, а не название комнаты.")
        return self.energy.device(self.session, device_id)

    def refresh_devices(self):
        current = self.tree.currentItem()
        previous_id = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self.tree.blockSignals(True)
        self.tree.clear()
        parents = {}
        for room in self.energy.rooms(self.session):
            item = QTreeWidgetItem([room["name"], "", "", "", ""])
            self.tree.addTopLevelItem(item)
            parents[room["id"]] = item
        for d in self.energy.devices(self.session):
            kind = {"meter": "Счётчик", "socket": "Умная розетка", "sensor": "Датчик"}[d["kind"]]
            item = QTreeWidgetItem(
                [
                    d["name"],
                    kind,
                    STATE_LABELS[d["state"]],
                    f"{d['power_w']:.0f} Вт",
                    d["last_seen"].strftime("%d.%m %H:%M:%S")
                    if d["last_seen"]
                    else "Нет показаний",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, d["id"])
            item.setToolTip(1, f"Серийный номер: {d['serial']}")
            if d["state"] == "emergency":
                item.setForeground(2, QColor("#b64634"))
            elif not d["online"]:
                item.setText(4, item.text(4) + " · не на связи")
            parents[d["room_id"]].addChild(item)
            if d["id"] == previous_id:
                self.tree.setCurrentItem(item)
        self.tree.expandAll()
        for index in range(4):
            self.tree.resizeColumnToContents(index)
        self.tree.blockSignals(False)
        owner = self.access.require(self.session)["role"] == Role.OWNER
        for b in self.device_owner_buttons:
            b.setVisible(owner)
        for w in [
            self.device_action,
            self.apply_device_action,
            self.manual_reading,
            self.inject_anomaly,
        ]:
            w.setVisible(owner)
        self.selection_changed()

    def selection_changed(self):
        try:
            d = self.selected_device()
        except DomainError:
            self.device_detail.setText("Выберите прибор в дереве.")
            self.apply_device_action.setEnabled(False)
            self.manual_reading.setEnabled(False)
            self.inject_anomaly.setEnabled(False)
            return
        self.device_detail.setText(
            f"{d['serial']} · Номинальная мощность {d['rated_w']:.0f} Вт · "
            f"Критический порог {d['critical_w']:.0f} Вт · "
            + ("Входит в итог дома" if d["counted"] else "Контрольный счётчик: исключён из итога")
        )
        actions = {
            "enable": "Включить питание",
            "start": "Начать работу",
            "finish": "Завершить цикл",
            "disable": "Выключить",
            "schedule": "Перенести на ночь",
            "reset": "Авария устранена: сбросить",
            "retire": "Списать устройство",
        }
        available = [
            (name, action)
            for action, name in actions.items()
            if action in TRANSITIONS[d["state"]] and (action != "schedule" or d["flexible"])
        ]
        self.refill_combo(self.device_action, available)
        self.apply_device_action.setEnabled(bool(available))
        self.manual_reading.setEnabled(True)
        self.inject_anomaly.setEnabled(d["serial"].startswith("DEMO-"))
        self.inject_anomaly.setToolTip("Тестовый скачок доступен только демонстрационным приборам")

    def add_room(self):
        name = line("Например, кухня")
        if form_dialog(self, "Добавить помещение", [("Название", name)]):
            self.energy.add_room(self.session, name.text())
            self.refresh_devices()

    def add_device(self):
        rooms = self.energy.rooms(self.session)
        if not rooms:
            raise ValidationError("Сначала добавьте помещение.")
        room = combo([(r["name"], r["id"]) for r in rooms])
        name, serial = line("Название электроприбора"), line("Уникальный серийный номер")
        kind = combo([("Умная розетка", "socket"), ("Счётчик", "meter"), ("Датчик", "sensor")])
        rated, critical = spin(1000, 1), spin(3000, 1)
        flexible, essential, counted = QCheckBox(), QCheckBox(), QCheckBox()
        counted.setChecked(True)
        if form_dialog(
            self,
            "Новое устройство учёта",
            [
                ("Помещение", room),
                ("Прибор", name),
                ("Серийный номер", serial),
                ("Тип", kind),
                ("Номинальная мощность, Вт", rated),
                ("Критический порог, Вт", critical),
                ("Гибкая нагрузка", flexible),
                ("Не отключать в сценариях", essential),
                ("Учитывать в итоге дома", counted),
            ],
        ):
            self.energy.add_device(
                self.session,
                room.currentData(),
                name.text(),
                serial.text(),
                kind.currentData(),
                rated.value(),
                critical.value(),
                flexible.isChecked(),
                essential.isChecked(),
                counted.isChecked(),
            )
            self.refresh_devices()

    def apply_state(self):
        d = self.selected_device()
        action = self.device_action.currentData()
        if action in ("retire", "reset"):
            text = (
                "Списать устройство? Его история сохранится."
                if action == "retire"
                else "Причина аварии устранена? После сброса прибор останется выключенным."
            )
            if QMessageBox.question(self, "Подтверждение", text) != QMessageBox.StandardButton.Yes:
                return
        self.energy.change_state(self.session, d["id"], action)
        self.refresh_devices()

    def add_reading(self):
        d = self.selected_device()
        measured = line()
        measured.setText((now() - timedelta(minutes=1)).isoformat(timespec="seconds"))
        power, energy, seconds = (
            spin(100, 0, 10_000_000),
            spin(0.001667, decimals=6),
            integer(60, 1, 86400),
        )
        power.valueChanged.connect(
            lambda: energy.setValue(power.value() / 1000 * seconds.value() / 3600)
        )
        seconds.valueChanged.connect(
            lambda: energy.setValue(power.value() / 1000 * seconds.value() / 3600)
        )
        if form_dialog(
            self,
            f"Показание: {d['name']}",
            [
                ("Начало интервала (ISO 8601)", measured),
                ("Интервал, секунд", seconds),
                ("Текущая мощность, Вт", power),
                ("Энергия за интервал, кВт·ч", energy),
            ],
        ):
            self.energy.ingest(
                self.session,
                ReadingPacket(
                    d["id"],
                    datetime.fromisoformat(measured.text()),
                    power.value(),
                    energy.value(),
                    seconds.value(),
                ),
            )
            self.refresh_devices()

    def test_anomaly(self):
        d = self.selected_device()
        if not d["serial"].startswith("DEMO-"):
            raise ValidationError("Проверка доступна только демонстрационным приборам.")
        # Данные теста не связаны с физическим прибором.
        if d["state"] not in ("idle", "running", "flexible"):
            raise ValidationError("Включите демонстрационный прибор перед проверкой защиты.")
        last = self.energy.db.one(
            "SELECT measured_at,interval_seconds FROM readings WHERE device_id=? "
            "ORDER BY measured_at DESC LIMIT 1",
            (d["id"],),
        )
        start = now() - timedelta(seconds=1)
        if last:
            start = max(
                start,
                datetime.fromisoformat(last["measured_at"])
                + timedelta(seconds=last["interval_seconds"]),
            )
        power = d["critical_w"] * 1.5
        self.energy.ingest(self.session, ReadingPacket(d["id"], start, power, power / 3_600_000, 1))
        self.refresh_devices()
        QMessageBox.information(
            self,
            "Проверка защиты",
            "Тестовый скачок записан. "
            "Результат проверки доступен в уведомлениях и состоянии прибора.",
        )

    def import_readings(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт показаний", "", "CSV (*.csv)")
        if path:
            count, errors = import_csv(self.energy, self.session, path)
            self.refresh_devices()
            QMessageBox.information(
                self,
                "Результат импорта",
                f"Принято: {count}. Ошибок: {len(errors)}.\n" + "\n".join(errors[:8]),
            )

    def event_log(self):
        rows = self.energy.events(self.session)
        dialog = QDialog(self)
        dialog.setWindowTitle("Журнал событий")
        dialog.resize(900, 550)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(
            "\n".join(
                f"{datetime.fromisoformat(r['created_at']).astimezone(LOCAL_TZ):%d.%m.%Y %H:%M:%S} "
                f"[{r['kind']}] {r['message']}"
                for r in rows
            )
        )
        layout.addWidget(view)
        layout.addWidget(button("Закрыть", dialog.accept))
        dialog.exec()

    def make_reports(self):
        layout = self.page(
            Screen.REPORTS,
            "Статистика и аналитика",
            "Расход по периодам, вклад приборов и рекомендации по экономии.",
        )
        filters = QHBoxLayout()
        self.period = combo([("День", "day"), ("Неделя", "week"), ("Месяц", "month")])
        self.period.setCurrentIndex(2)
        self.report_date = QDateEdit(QDate(now().year, now().month, now().day))
        self.report_date.setCalendarPopup(True)
        self.report_date.setDisplayFormat("dd.MM.yyyy")
        self.report_room = combo([("Все комнаты", None)])
        self.report_device = combo([("Все приборы", None)])
        for w in (self.period, self.report_date, self.report_room, self.report_device):
            filters.addWidget(w)
        filters.addWidget(button("Построить", lambda: self.guard(self.refresh_reports), True))
        filters.addWidget(button("CSV ↓", lambda: self.guard(lambda: self.export_report("csv"))))
        filters.addWidget(button("PDF ↓", lambda: self.guard(lambda: self.export_report("pdf"))))
        layout.addLayout(filters)
        self.report_room.currentIndexChanged.connect(lambda: self.guard(self.refresh_reports))
        self.report_summary = label("", "section")
        layout.addWidget(self.report_summary)
        self.report_chart = EnergyChart()
        layout.addWidget(self.report_chart)
        self.report_table = table(["Прибор", "Помещение", "Расход, кВт·ч", "Стоимость, BYN"])
        self.report_table.setMinimumHeight(180)
        layout.addWidget(self.report_table)
        self.report_notes = label("")
        layout.addWidget(self.report_notes)
        layout.addWidget(
            button("← Назад в главное меню", lambda: self.guard(lambda: self.navigate(Screen.HOME)))
        )

    def refresh_reports(self):
        self.access.require(self.session)
        self.refill_combo(
            self.report_room,
            [("Все комнаты", None)]
            + [(r["name"], r["id"]) for r in self.energy.rooms(self.session)],
        )
        room_id = self.report_room.currentData()
        self.refill_combo(
            self.report_device,
            [("Все приборы", None)]
            + [
                (d["name"], d["id"])
                for d in self.energy.devices(self.session)
                if room_id is None or d["room_id"] == room_id
            ],
        )
        date = self.report_date.date()
        anchor = datetime(date.year(), date.month(), date.day(), tzinfo=LOCAL_TZ)
        report = self.reports.build(
            self.session,
            self.period.currentData(),
            anchor,
            room_id,
            self.report_device.currentData(),
        )
        self.current_report = report
        change = (
            f"{report.change_percent:+.1f}%" if report.change_percent is not None else "нет данных"
        )
        self.report_summary.setText(
            f"{report.energy_kwh:.2f} кВт·ч  ·  {report.cost:.2f} BYN  ·  "
            f"К предыдущему периоду: {change}"
        )
        self.report_chart.update_data(report.series)
        fill_table(
            self.report_table,
            [
                (d["name"], d["room"], f"{d['energy_kwh']:.3f}", f"{d['cost']:.2f}")
                for d in report.devices
            ],
        )
        anomalies = [f"⚠ {a['message']}" for a in report.anomalies[:4]]
        self.report_notes.setText(
            "\n".join(
                [
                    f"{report.start:%d.%m.%Y %H:%M} — {report.end:%d.%m.%Y %H:%M} · Минск. "
                    "Незавершённые периоды сравниваются за одинаковое прошедшее время.",
                    *anomalies,
                    *["• " + r for r in report.recommendations],
                ]
            )
        )

    def export_report(self, kind):
        self.refresh_reports()
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить отчёт", f"energy-report.{kind}", f"{kind.upper()} (*.{kind})"
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != "." + kind:
            target = target.with_suffix("." + kind)
        if kind == "csv":
            self.reports.export_csv(self.session, self.current_report, target)
        else:
            document = QTextDocument()
            document.setHtml(self.reports.html(self.session, self.current_report))
            writer = QPdfWriter(str(target))
            writer.setResolution(96)
            document.print_(writer)
            del writer
            if not target.exists() or target.stat().st_size == 0:
                raise ValidationError("Не удалось записать PDF в выбранную папку.")
            self.reports.record_export(self.user, self.current_report, "pdf")
        self.statusBar().showMessage(f"Отчёт сохранён: {target}", 10_000)

    def make_settings(self):
        layout = self.page(Screen.SETTINGS, "Настройки дома", "Управление доступно владельцу дома.")
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        tariff_page = QWidget()
        form = QFormLayout(tariff_page)
        form.setContentsMargins(24, 24, 24, 24)
        form.setVerticalSpacing(16)
        self.budget_input = spin(100, 0.01)
        self.daily_input = spin(25, 0.01)
        self.day_rate = spin(0.28, decimals=4)
        self.night_rate = spin(0.14, decimals=4)
        self.day_start = integer(7)
        self.night_start = integer(23)
        self.notify_80 = QCheckBox("Уведомлять при достижении 80% месячного лимита")
        self.auto_protect = QCheckBox(
            "Аварийно обесточивать прибор в модели при критической мощности"
        )
        for title, widget in [
            ("Месячный лимит, BYN", self.budget_input),
            ("Дневной лимит, кВт·ч", self.daily_input),
            ("День, BYN/кВт·ч", self.day_rate),
            ("Ночь, BYN/кВт·ч", self.night_rate),
            ("Начало дня, час", self.day_start),
            ("Начало ночи, час", self.night_start),
            ("", self.notify_80),
            ("", self.auto_protect),
        ]:
            form.addRow(title, widget)
        form.addRow(
            label(
                "Изменения тарифов действуют с момента сохранения. "
                "Ранее принятые показания сохраняют свою стоимость.",
                "subtitle",
            )
        )
        self.tabs.addTab(tariff_page, "Тарифы и лимиты")
        self.make_family_tab()
        self.make_automation_tab()
        self.make_building_tab()
        self.make_integration_tab()
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button("Отмена", lambda: self.guard(lambda: self.navigate(Screen.HOME))))
        actions.addWidget(
            button("Сохранить и закрыть", lambda: self.guard(self.save_settings), True)
        )
        layout.addLayout(actions)

    def refresh_settings(self):
        settings = self.energy.settings(self.session)
        for field, widget in [
            ("monthly_budget", self.budget_input),
            ("daily_kwh", self.daily_input),
            ("day_rate", self.day_rate),
            ("night_rate", self.night_rate),
            ("day_start", self.day_start),
            ("night_start", self.night_start),
        ]:
            widget.setValue(settings[field])
        self.notify_80.setChecked(bool(settings["notify_80"]))
        self.auto_protect.setChecked(bool(settings["auto_protect"]))
        self.scenario.setCurrentIndex(max(0, self.scenario.findData(settings["scenario"])))
        self.refresh_family()
        self.refresh_building()

    def save_settings(self):
        self.energy.save_settings(
            self.session,
            monthly_budget=self.budget_input.value(),
            daily_kwh=self.daily_input.value(),
            day_rate=self.day_rate.value(),
            night_rate=self.night_rate.value(),
            day_start=self.day_start.value(),
            night_start=self.night_start.value(),
            notify_80=self.notify_80.isChecked(),
            auto_protect=self.auto_protect.isChecked(),
        )
        self.navigate(Screen.HOME)

    def make_family_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.addWidget(label("Семья и доступ", "section"))
        self.family_table = table(["Имя", "Логин", "Роль", "Доступ"])
        layout.addWidget(self.family_table)
        row = QHBoxLayout()
        row.addWidget(button("+ Добавить члена семьи", lambda: self.guard(self.add_family)))
        row.addWidget(button("Включить / отключить доступ", lambda: self.guard(self.toggle_family)))
        layout.addLayout(row)
        layout.addWidget(
            label(
                "Члены семьи видят потребление, отчёты и уведомления. "
                "Изменять устройства, настройки и доступ может только владелец.",
                "subtitle",
            )
        )
        self.tabs.addTab(page, "Семья")

    def refresh_family(self):
        self.family_rows = self.access.family(self.session)
        fill_table(
            self.family_table,
            [
                (
                    u["name"],
                    u["login"],
                    "Владелец" if u["role"] == "owner" else "Член семьи",
                    "Включён" if u["active"] else "Отключён",
                )
                for u in self.family_rows
            ],
        )

    def add_family(self):
        name, login, password = line(), line(), line(password=True)
        if form_dialog(
            self, "Новый член семьи", [("Имя", name), ("Логин", login), ("Пароль", password)]
        ):
            self.access.add_family(self.session, name.text(), login.text(), password.text())
            self.refresh_family()

    def toggle_family(self):
        row = self.family_table.currentRow()
        if row < 0:
            raise ValidationError("Выберите члена семьи.")
        user = self.family_rows[row]
        self.access.set_family_active(self.session, user["id"], not user["active"])
        self.refresh_family()

    def make_automation_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(label("Сценарии и планировщик", "section"))
        layout.addWidget(
            label(
                "Эко-режим выключает ожидающие приборы и переносит гибкую нагрузку "
                "на ночь. Отпуск выключает некритичные приборы. Приборы с отметкой "
                "«Не отключать в сценариях» продолжают работать."
            )
        )
        self.scenario = combo(
            [
                ("Ручное управление", "manual"),
                ("Эко-режим", "eco"),
                ("Отпуск", "vacation"),
                ("Ночной тариф", "night_tariff"),
            ]
        )
        layout.addWidget(self.scenario)
        layout.addWidget(
            button("Применить сценарий сейчас", lambda: self.guard(self.apply_scenario), True)
        )
        layout.addWidget(
            label(
                "Планировщик запускает отложенную нагрузку с наступлением ночной зоны, "
                "пока приложение открыто и владелец авторизован. "
                "Ручной режим не включает ранее выключенные приборы автоматически.",
                "subtitle",
            )
        )
        layout.addStretch()
        self.tabs.addTab(page, "Автоматизация")

    def apply_scenario(self):
        changed = self.automation.apply(self.session, self.scenario.currentData())
        QMessageBox.information(self, "Сценарий применён", f"Изменено приборов: {changed}.")

    def make_building_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.addWidget(label("Многоквартирный дом и общие зоны", "section"))
        layout.addWidget(
            label(
                "Введите показания квартир и общий расход за один и тот же период. "
                "Расчёт общих зон ведётся отдельно от личного счётчика дома.",
                "subtitle",
            )
        )
        row = QHBoxLayout()
        self.common_energy = spin(100)
        self.allocation = combo(
            [("По площади", "area"), ("По числу жильцов", "residents"), ("Поровну", "equal")]
        )
        row.addWidget(label("Общие зоны, кВт·ч"))
        row.addWidget(self.common_energy)
        row.addWidget(self.allocation)
        row.addWidget(button("Рассчитать", lambda: self.guard(self.refresh_building)))
        layout.addLayout(row)
        self.building_table = table(
            [
                "Квартира",
                "Площадь, м²",
                "Личный расход",
                "Доля общих зон",
                "Итого, кВт·ч",
                "Экономнее среднего",
            ]
        )
        layout.addWidget(self.building_table)
        layout.addWidget(button("+ Добавить квартиру", lambda: self.guard(self.add_apartment)))
        self.tabs.addTab(page, "Общие зоны")

    def add_apartment(self):
        name, area, residents, energy = line(), spin(50, 0.1), integer(2, 1, 100), spin(100)
        if form_dialog(
            self,
            "Добавить квартиру",
            [
                ("Название / номер", name),
                ("Площадь, м²", area),
                ("Жильцы", residents),
                ("Собственный расход, кВт·ч", energy),
            ],
        ):
            self.building.add_apartment(
                self.session, name.text(), area.value(), residents.value(), energy.value()
            )
            self.refresh_building()

    def refresh_building(self):
        rows = self.building.distribute(
            self.session, self.common_energy.value(), self.allocation.currentData()
        )
        fill_table(
            self.building_table,
            [
                (
                    a["name"],
                    f"{a['area']:.1f}",
                    f"{a['own_kwh']:.2f}",
                    f"{a['common_kwh']:.2f}",
                    f"{a['total_kwh']:.2f}",
                    f"{a['efficiency_percent']:+.1f}%",
                )
                for a in rows
            ],
        )

    def make_integration_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(label("Home Assistant", "section"))
        form = QFormLayout()
        self.ha_url, self.ha_token, self.ha_entity = (
            line("http://homeassistant.local:8123"),
            line(password=True),
            line("sensor.power"),
        )
        form.addRow("Адрес сервера", self.ha_url)
        form.addRow("Токен (не сохраняется)", self.ha_token)
        form.addRow("Датчик мощности", self.ha_entity)
        layout.addLayout(form)
        self.ha_read_button = button(
            "Проверить и прочитать мощность", lambda: self.guard(self.read_home_assistant)
        )
        layout.addWidget(self.ha_read_button)
        self.ha_result = label("", "section")
        layout.addWidget(self.ha_result)
        layout.addWidget(
            label(
                "Чтение датчика выполняется по запросу. Для массового учёта используйте "
                "CSV с интервальной энергией. Яндекс.Алиса и Google Home подключаются "
                "через адаптеры; внешние учётные записи в демо не используются.",
                "subtitle",
            )
        )
        layout.addWidget(button("Открыть помощника", lambda: self.guard(self.voice_dialog)))
        layout.addStretch()
        self.tabs.addTab(page, "Интеграции")

    def read_home_assistant(self):
        self.access.require(self.session, owner=True)
        adapter = HomeAssistantAdapter(self.ha_url.text(), self.ha_token.text())
        entity = self.ha_entity.text().strip()
        adapter._entity(entity)
        self.ha_future = self.network_pool.submit(adapter.power_w, entity)
        self.ha_read_button.setEnabled(False)
        self.ha_result.setText("Подключение…")
        self.ha_poll.start()

    def poll_home_assistant(self):
        if not self.ha_future or not self.ha_future.done():
            return
        future, self.ha_future = self.ha_future, None
        self.ha_poll.stop()
        self.ha_read_button.setEnabled(True)
        try:
            self.access.require(self.session, owner=True)
            self.ha_result.setText(f"Получено: {future.result():.2f} Вт")
        except (DomainError, ValueError, OSError) as exc:
            self.ha_result.setText(str(exc))

    def voice_dialog(self):
        command = line("Например: текущее потребление")
        if form_dialog(
            self,
            "Помощник: текст распознанной команды",
            [
                ("Команда", command),
                (
                    "Доступные команды",
                    label("текущее потребление; отчёт за месяц; эко-режим; отпуск; ночной тариф"),
                ),
            ],
            accept="Выполнить",
        ):
            response = self.assistant.execute(
                self.session, self.assistant.recognize(command.text())
            )
            QMessageBox.information(self, "Помощник", response)

    def closeEvent(self, event):
        self.timer.stop()
        self.ha_poll.stop()
        self.network_pool.shutdown(wait=False, cancel_futures=True)
        if self.session:
            self.access.logout(self.session)
        self.ha_token.clear()
        event.accept()
