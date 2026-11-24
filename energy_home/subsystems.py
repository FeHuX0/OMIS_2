"""Subsystems — 1:1 implementation of Base Class Diagram 4.1 (page 13) and Sequence 3.3 (page 12)."""

from __future__ import annotations

import secrets
import time
from datetime import datetime
from typing import Any, Optional

from energy_home.domain import (
    LOCAL_TZ,
    AccessDeniedError,
    Anomaly,
    Appliance,
    Chart,
    Event,
    Home,
    Limit,
    MeteringDevice,
    Notification,
    Reading,
    Report,
    Role,
    Room,
    Tariff,
    User,
    ValidationError,
    hash_password,
)
from energy_home.state_machine import (
    DeviceState,
    WindowState,
    WindowTransitionFSM,
)


class StorageSubsystem:
    """<<infrastructure>> UML `Подсистема хранения` (4.1, page 13): save(obj), find(criteria)."""

    def __init__(self) -> None:
        self.users: list[User] = []
        self.homes: list[Home] = []
        self.tariffs: list[Tariff] = []
        self.limits: list[Limit] = []
        self.readings: list[Reading] = []
        self.events: list[Event] = []
        self.anomalies: list[Anomaly] = []
        self.notifications: list[Notification] = []
        self.reports: list[Report] = []
        self._period_series: dict[str, dict[str, Any]] = {}
        self._room_seq = 0
        self._appliance_seq = 0

    def save(self, obj: Any) -> None:
        """UML method `+сохранить(объект): void`."""
        if isinstance(obj, User):
            if obj not in self.users:
                self.users.append(obj)
        elif isinstance(obj, Home):
            if obj not in self.homes:
                self.homes.append(obj)
        elif isinstance(obj, Tariff):
            self.tariffs.append(obj)
        elif isinstance(obj, Limit):
            self.limits.append(obj)
        elif isinstance(obj, Reading):
            self.readings.append(obj)
        elif isinstance(obj, Event):
            self.events.append(obj)
        elif isinstance(obj, Anomaly):
            self.anomalies.append(obj)
        elif isinstance(obj, Notification):
            self.notifications.append(obj)
        elif isinstance(obj, Report):
            self.reports.append(obj)
        else:
            raise ValidationError(f"Неподдерживаемый тип объекта для хранения: {type(obj).__name__}")

    def find(self, criteria: Any) -> list[Any]:
        """UML method `+найти(критерий): List`."""
        if callable(criteria):
            all_items = (
                self.users
                + self.homes
                + self.tariffs
                + self.limits
                + self.readings
                + self.events
                + self.anomalies
                + self.notifications
                + self.reports
            )
            return [x for x in all_items if criteria(x)]

        collection_map: dict[Any, list[Any]] = {
            User: self.users,
            Home: self.homes,
            Tariff: self.tariffs,
            Limit: self.limits,
            Reading: self.readings,
            Event: self.events,
            Anomaly: self.anomalies,
            Notification: self.notifications,
            Report: self.reports,
            "users": self.users,
            "homes": self.homes,
            "tariffs": self.tariffs,
            "limits": self.limits,
            "readings": self.readings,
            "events": self.events,
            "anomalies": self.anomalies,
            "notifications": self.notifications,
            "reports": self.reports,
        }

        if isinstance(criteria, dict):
            obj_type = criteria.get("type")
            items = collection_map.get(obj_type, [])
            result = []
            for item in items:
                matched = True
                for key, value in criteria.items():
                    if key == "type":
                        continue
                    if getattr(item, key, None) != value:
                        matched = False
                        break
                if matched:
                    result.append(item)
            return result

        return list(collection_map.get(criteria, []))

    def active_tariff(self) -> Tariff:
        if not self.tariffs:
            tariff = Tariff()
            self.save(tariff)
            return tariff
        return self.tariffs[-1]

    def active_limit(self) -> Limit:
        if not self.limits:
            limit = Limit()
            self.save(limit)
            return limit
        return self.limits[-1]

    def next_room_id(self) -> int:
        self._room_seq += 1
        return self._room_seq

    def next_appliance_id(self) -> int:
        self._appliance_seq += 1
        return self._appliance_seq


class AccessControlSubsystem:
    """<<control>> UML `Подсистема управления доступом` (4.1, page 13): check_permissions(user, action)."""

    ROLE_PERMISSIONS: dict[Role, set[str]] = {
        Role.OWNER: {
            "view_consumption",
            "view_notifications",
            "view_reports",
            "view_devices",
            "configure_tariffs_and_limits",
            "register_devices",
            "register_rooms",
            "manage_devices",
            "manage_access",
        },
        Role.FAMILY_MEMBER: {
            "view_consumption",
            "view_notifications",
            "view_reports",
            "view_devices",
        },
    }

    def __init__(self, storage: StorageSubsystem) -> None:
        self.storage = storage
        self._sessions: dict[str, User] = {}
        self._failed_attempts: dict[str, list[float]] = {}

    def check_permissions(self, user: Optional[User], action: str) -> bool:
        """UML method `+проверитьПрава(пользователь, действие): Boolean`."""
        if user is None or not user.active:
            return False
        return action in self.ROLE_PERMISSIONS.get(user.role, set())

    def require_permission(self, user: Optional[User], action: str) -> None:
        if not self.check_permissions(user, action):
            if action == "configure_tariffs_and_limits":
                raise AccessDeniedError("Настройки доступны только владельцу дома ([Роль == Владелец]).")
            raise AccessDeniedError("Недостаточно прав для выполнения операции.")

    def login(self, login: str, password: str) -> tuple[str, User]:
        key = (login or "").strip().lower()
        now = time.time()
        attempts = [t for t in self._failed_attempts.get(key, []) if now - t < 60.0]
        if len(attempts) >= 5:
            raise AccessDeniedError("Слишком много неудачных попыток входа. Повторите через минуту.")

        candidates = [u for u in self.storage.users if u.login.lower() == key]
        user = candidates[0] if candidates else None
        if user is None or not user.authenticate(password):
            attempts.append(now)
            self._failed_attempts[key] = attempts
            self.storage.save(
                Event(
                    event_type="auth_failed",
                    timestamp=datetime.now(LOCAL_TZ),
                    description=f"Неудачный вход: {key}",
                )
            )
            raise AccessDeniedError("Неверный логин или пароль.")

        self._failed_attempts.pop(key, None)
        token = secrets.token_urlsafe(32)
        self._sessions[token] = user
        self.storage.save(
            Event(
                event_type="login",
                timestamp=datetime.now(LOCAL_TZ),
                description="Вход пользователя",
                user_login=user.login,
            )
        )
        return token, user

    def logout(self, token: Optional[str]) -> None:
        if token and token in self._sessions:
            user = self._sessions.pop(token)
            self.storage.save(
                Event(
                    event_type="logout",
                    timestamp=datetime.now(LOCAL_TZ),
                    description="Выход",
                    user_login=user.login,
                )
            )

    def get_user(self, token: Optional[str]) -> User:
        if not token or token not in self._sessions:
            raise AccessDeniedError("Требуется авторизация.")
        user = self._sessions[token]
        if not user.active:
            raise AccessDeniedError("Учётная запись отключена.")
        return user

    def register(
        self,
        login: str,
        password: str,
        name: str = "",
        role: Role = Role.OWNER,
        home: Optional[Home] = None,
    ) -> tuple[str, User]:
        clean_login = (login or "").strip().lower()
        if not clean_login or "@" not in clean_login or len(clean_login) > 120:
            raise ValidationError("Укажите корректный Login / E-mail.")
        if any(u.login.lower() == clean_login for u in self.storage.users):
            raise ValidationError("Пользователь с таким Login / E-mail уже существует.")
        encoded = hash_password(password)
        target_home = home
        if target_home is None:
            if role == Role.FAMILY_MEMBER and self.storage.homes:
                target_home = self.storage.homes[0]
            else:
                target_home = Home(address="Мой дом", rooms=[])
                self.storage.save(target_home)
        user = User(
            login=clean_login,
            password=encoded,
            role=role,
            home=target_home,
            name=(name or clean_login.split("@")[0]).strip(),
        )
        self.storage.save(user)
        self.storage.save(
            Event(
                event_type="registration",
                timestamp=datetime.now(LOCAL_TZ),
                description=f"Зарегистрирован пользователь {clean_login} ({role.value})",
                user_login=clean_login,
            )
        )
        return self.login(clean_login, password)


class DataCollectionSubsystem:
    """<<control>> UML `Подсистема сбора данных` (4.1, page 13): poll_devices(), save_readings()."""

    def __init__(self, storage: StorageSubsystem) -> None:
        self.storage = storage
        self._reading_buffer: list[Reading] = []

    def poll_devices(self, home: Optional[Home] = None) -> list[Reading]:
        """UML method `+опроситьУстройства(): List`."""
        target_home = home or (self.storage.homes[0] if self.storage.homes else None)
        if target_home is None:
            return []
        collected: list[Reading] = []
        tariff = self.storage.active_tariff()
        for room in target_home.rooms:
            for appliance in room.appliances:
                device = appliance.metering_device
                if device is None:
                    continue
                device.tariff = tariff
                if appliance.state_machine.outer_state == DeviceState.ON:
                    reading = device.read_measurement(power_w=appliance.current_power_w())
                    reading.room_name = room.name
                    reading.appliance_name = appliance.name
                    collected.append(reading)
        self._reading_buffer.extend(collected)
        return collected

    def save_readings(self, readings: Optional[list[Reading]] = None) -> None:
        """UML method `+сохранитьПоказания(): void` (also BPMN validation step, page 10)."""
        items = self._reading_buffer if readings is None else readings
        for r in list(items):
            if r.value < 0 or r.power_w < 0:
                self.storage.save(
                    Event(
                        event_type="validation_error",
                        timestamp=datetime.now(LOCAL_TZ),
                        description=f"Некорректное показание: значение={r.value}, мощность={r.power_w}",
                    )
                )
                continue
            if r.tariff is None:
                r.tariff = self.storage.active_tariff()
            self.storage.save(r)
        if readings is None:
            self._reading_buffer.clear()


class AnalysisSubsystem:
    """<<control>> UML `Подсистема анализа` (4.1, page 13): calculate_consumption(), detect_anomalies()."""

    def __init__(self, storage: StorageSubsystem) -> None:
        self.storage = storage

    def calculate_consumption(self, home: Optional[Home] = None, period: Optional[str] = None) -> float:
        """UML method `+рассчитатьРасход(): Double`."""
        if period and period in self.storage._period_series:
            values = self.storage._period_series[period]["values"]
            return round(sum(float(v) for v in values), 2)
        target_home = home or (self.storage.homes[0] if self.storage.homes else None)
        if target_home is not None:
            return target_home.get_total_consumption()
        return round(sum(r.value for r in self.storage.readings), 2)

    def detect_anomalies(
        self,
        home: Optional[Home] = None,
        spike_multiplier: float = 1.2,
    ) -> list[Anomaly]:
        """UML method `+выявитьАномалии(): List`."""
        found: list[Anomaly] = list(self.storage.anomalies)
        target_home = home or (self.storage.homes[0] if self.storage.homes else None)
        if target_home is None:
            return found

        for room in target_home.rooms:
            for appliance in room.appliances:
                if appliance.metering_device is None or appliance.power <= 0:
                    continue
                for reading in appliance.metering_device.readings:
                    if reading.power_w > appliance.critical_power_w or (
                        reading.power_w > appliance.power * spike_multiplier and reading.power_w > 500
                    ):
                        deviation = ((reading.power_w / max(appliance.power, 1.0)) - 1.0) * 100.0
                        loc = f"линии {room.name.lower()}"
                        exists = any(a.timestamp == reading.timestamp and a.location == loc for a in found)
                        if not exists:
                            anomaly = Anomaly(
                                anomaly_type="Скачок мощности",
                                magnitude=round(deviation, 1),
                                timestamp=reading.timestamp,
                                location=loc,
                            )
                            self.storage.save(anomaly)
                            found.append(anomaly)
        return found

    def check_limits(self, home: Home) -> list[str]:
        """Check daily energy limit and 80%/100% monthly budget thresholds."""
        limit = self.storage.active_limit()
        tariff = self.storage.active_tariff()
        daily_kwh = self.calculate_consumption(home)
        alerts: list[str] = []
        if daily_kwh > limit.daily_limit_kwh:
            alerts.append("Внимание! Превышен лимит дневного потребления энергии.")
        estimated_cost_byn = daily_kwh * tariff.day_rate
        if estimated_cost_byn >= limit.monthly_budget_byn:
            alerts.append(
                f"Внимание! Превышен месячный лимит расходов ({limit.monthly_budget_byn:.0f} бел. руб.)."
            )
        elif limit.notify_at_80 and estimated_cost_byn >= limit.monthly_budget_byn * 0.8:
            alerts.append("Внимание! Достигнуто 80% месячного лимита расходов.")
        return alerts


class NotificationSubsystem:
    """<<control>> UML `Подсистема уведомлений` (4.1, page 13): send_notification()."""

    def __init__(self, storage: StorageSubsystem, analysis: AnalysisSubsystem) -> None:
        self.storage = storage
        self.analysis = analysis

    def send_notification(self, message: Optional[str] = None, notification_type: str = "warning") -> None:
        """UML method `+отправитьУведомление(): void`."""
        if message is None:
            anomalies = self.analysis.detect_anomalies()
            if anomalies:
                message = anomalies[-1].describe()
                notification_type = "anomaly"
            else:
                message = "Внимание! Превышен лимит дневного потребления энергии."
                notification_type = "limit"
        notification = Notification(
            message=message,
            notification_type=notification_type,
            sent_at=datetime.now(LOCAL_TZ),
        )
        self.storage.save(notification)


class ReportingSubsystem:
    """<<boundary>> UML `Подсистема отчётности` (4.1, page 13): generate_report(), build_chart()."""

    def __init__(self, storage: StorageSubsystem, analysis: AnalysisSubsystem) -> None:
        self.storage = storage
        self.analysis = analysis

    def build_chart(self, period: str = "week") -> Chart:
        """UML method `+построитьГрафик(): Chart`."""
        data = self.storage._period_series.get(period)
        if data:
            return Chart(
                title=data["title"],
                subtitle=data["subtitle"],
                period=period,
                labels=list(data["labels"]),
                values=[float(v) for v in data["values"]],
                y_step=float(data.get("y_step", 5.0)),
                y_max=float(data.get("y_max", 25.0)),
            )
        return Chart(
            title="График расхода по дням (кВт·ч)",
            subtitle="Сегодня: 22.03.2027 - Пятница",
            period=period,
            labels=["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
            values=[16.5, 13.3, 23.3, 15.1, 7.8, 0.0, 0.0],
            y_step=5.0,
            y_max=25.0,
        )

    def generate_report(self, period: str = "week", home: Optional[Home] = None) -> Report:
        """UML method `+сформироватьОтчет(): Отчет`."""
        chart = self.build_chart(period)
        consumption = self.analysis.calculate_consumption(home=home, period=period)
        tariff = self.storage.active_tariff()
        cost = round(consumption * ((tariff.day_rate + tariff.night_rate) / 2.0), 2)
        anomalies = self.analysis.detect_anomalies(home=home)
        recommendations: list[str] = []
        if anomalies:
            recommendations.append("Проверьте линию с выявленным скачком мощности.")
        report = Report(
            period=period,
            generated_at=datetime.now(LOCAL_TZ),
            total_consumption_kwh=consumption,
            total_cost_byn=cost,
            chart=chart,
            anomalies=anomalies,
            recommendations=recommendations,
        )
        self.storage.save(report)
        return report


class SensorGateway:
    """Sensor data gateway per Sequence Diagram 3.3 (page 12)."""

    def __init__(
        self,
        collection: DataCollectionSubsystem,
        analysis: AnalysisSubsystem,
        notifications: NotificationSubsystem,
        storage: StorageSubsystem,
    ) -> None:
        self.collection = collection
        self.analysis = analysis
        self.notifications = notifications
        self.storage = storage

    def submit_reading(self, appliance: Appliance, reading: Reading, home: Home) -> dict[str, Any]:
        """Sequence 3.3 steps 1..11: validate -> save -> analyze -> notify or log normal."""
        if reading.value < 0 or reading.power_w < 0:
            self.storage.save(
                Event(
                    event_type="invalid_reading",
                    timestamp=datetime.now(LOCAL_TZ),
                    description="Отрицательные значения пакета",
                )
            )
            raise ValidationError("Показание должно быть неотрицательным.")

        if appliance.metering_device is not None:
            appliance.metering_device.readings.append(reading)
            appliance.metering_device.current_power_w = reading.power_w
        self.collection.save_readings([reading])

        critical_exceeded = reading.power_w > appliance.critical_power_w
        alerts = self.analysis.check_limits(home)
        if critical_exceeded:
            if appliance.state_machine.outer_state == DeviceState.ON:
                appliance.state_machine.trigger_anomaly(
                    critical_threshold_exceeded=True,
                    notify_callback=lambda: self.notifications.send_notification(
                        f"Критический скачок мощности на «{appliance.name}» ({int(reading.power_w)} Вт). Линия обесточена.",
                        notification_type="anomaly",
                    ),
                )
            anomaly = Anomaly(
                anomaly_type="Скачок мощности",
                magnitude=round(((reading.power_w / max(appliance.power, 1.0)) - 1.0) * 100.0, 1),
                timestamp=reading.timestamp,
                location=f"линии прибора {appliance.name}",
            )
            self.storage.save(anomaly)
            return {"status": "alert_sent", "anomaly": anomaly.describe()}

        if alerts:
            self.notifications.send_notification(alerts[0], notification_type="limit")
            return {"status": "alert_sent", "message": alerts[0]}

        self.storage.save(
            Event(
                event_type="normal_operation",
                timestamp=datetime.now(LOCAL_TZ),
                description=f"Показание прибора «{appliance.name}» обработано",
            )
        )
        return {"status": "processed_ok"}


class ApplicationInterface:
    """<<boundary>> UML `Интерфейс приложения` (4.1, page 13): show_consumption(), configure_limit()."""

    def __init__(
        self,
        storage: StorageSubsystem,
        collection: DataCollectionSubsystem,
        analysis: AnalysisSubsystem,
        notifications: NotificationSubsystem,
        reporting: ReportingSubsystem,
        access_control: AccessControlSubsystem,
    ) -> None:
        self.storage = storage
        self.collection = collection
        self.analysis = analysis
        self.notifications = notifications
        self.reporting = reporting
        self.access_control = access_control
        self.gateway = SensorGateway(collection, analysis, notifications, storage)
        self._window_fsms: dict[str, WindowTransitionFSM] = {}

    def window_fsm(self, token: str) -> WindowTransitionFSM:
        if token not in self._window_fsms:
            self._window_fsms[token] = WindowTransitionFSM(current_window=WindowState.MAIN_WINDOW)
        return self._window_fsms[token]

    def show_consumption(self, user: Optional[User] = None) -> dict[str, Any]:
        """UML method `+показатьПотребление(): void`."""
        if user is not None:
            self.access_control.require_permission(user, "view_consumption")
            home = user.home
        else:
            home = self.storage.homes[0] if self.storage.homes else None
        if home is None:
            return {"current_power_kw": 0.0, "daily_consumption_kwh": 0.0, "alerts": []}

        return {
            "current_power_kw": home.current_power_kw(),
            "daily_consumption_kwh": self.analysis.calculate_consumption(home),
            "alerts": self.analysis.check_limits(home),
        }

    def configure_limit(
        self,
        user: Optional[User] = None,
        monthly_budget_byn: Optional[float] = None,
        day_rate: Optional[float] = None,
        night_rate: Optional[float] = None,
        notify_at_80: Optional[bool] = None,
    ) -> dict[str, Any]:
        """UML method `+настроитьЛимит(): void` (Mockup 5.3.5, page 20)."""
        if user is not None:
            self.access_control.require_permission(user, "configure_tariffs_and_limits")

        limit = self.storage.active_limit()
        tariff = self.storage.active_tariff()

        if monthly_budget_byn is not None:
            if monthly_budget_byn <= 0:
                raise ValidationError("Месячный лимит расходов должен быть больше 0.")
            limit.monthly_budget_byn = float(monthly_budget_byn)

        if notify_at_80 is not None:
            limit.notify_at_80 = bool(notify_at_80)

        new_day = tariff.day_rate if day_rate is None else float(day_rate)
        new_night = tariff.night_rate if night_rate is None else float(night_rate)
        if new_day < 0 or new_night < 0:
            raise ValidationError("Стоимость тарифа не может быть отрицательной.")
        if new_day != tariff.day_rate or new_night != tariff.night_rate:
            new_tariff = Tariff(
                day_rate=new_day,
                night_rate=new_night,
                day_start=tariff.day_start,
                night_start=tariff.night_start,
            )
            self.storage.save(new_tariff)
            tariff = new_tariff

        self.storage.save(
            Event(
                event_type="settings_changed",
                timestamp=datetime.now(LOCAL_TZ),
                description=f"Лимит: {limit.monthly_budget_byn} руб., тарифы: {tariff.day_rate}/{tariff.night_rate}",
                user_login=user.login if user else None,
            )
        )
        return {
            "monthly_budget_byn": limit.monthly_budget_byn,
            "day_rate": tariff.day_rate,
            "night_rate": tariff.night_rate,
            "notify_at_80": limit.notify_at_80,
        }

    def add_room(self, user: User, name: str) -> Room:
        self.access_control.require_permission(user, "register_rooms")
        clean_name = (name or "").strip()
        if not clean_name or len(clean_name) > 80:
            raise ValidationError("Введите название помещения (от 1 до 80 символов).")
        home = user.home
        if home is None:
            raise ValidationError("У пользователя не привязан дом.")
        if any(r.name.lower() == clean_name.lower() for r in home.rooms):
            raise ValidationError("Помещение с таким названием уже существует.")
        room = Room(name=clean_name, id=self.storage.next_room_id())
        home.add_room(room)
        self.storage.save(
            Event(
                event_type="room_added",
                timestamp=datetime.now(LOCAL_TZ),
                description=f"Добавлено помещение: {clean_name}",
                user_login=user.login,
            )
        )
        return room

    def add_device(
        self,
        user: User,
        room_id: int,
        name: str,
        device_type: str,
        serial_number: str,
        power_w: float,
    ) -> Appliance:
        self.access_control.require_permission(user, "register_devices")
        home = user.home
        if home is None:
            raise ValidationError("У пользователя не привязан дом.")
        room = next((r for r in home.rooms if r.id == room_id), None)
        if room is None:
            raise ValidationError("Выберите существующее помещение.")
        clean_name = (name or "").strip()
        clean_serial = (serial_number or "").strip()
        if not clean_name or len(clean_name) > 80:
            raise ValidationError("Укажите название устройства/прибора (до 80 символов).")
        if not clean_serial or len(clean_serial) > 60:
            raise ValidationError("Укажите серийный номер устройства учёта.")
        if power_w < 0 or power_w > 50000:
            raise ValidationError("Мощность должна быть в диапазоне от 0 до 50000 Вт.")

        device = MeteringDevice(
            device_type=(device_type or "Умная розетка").strip(),
            serial_number=clean_serial,
            tariff=self.storage.active_tariff(),
            current_power_w=float(power_w),
            active=True,
        )
        appliance = Appliance(
            name=clean_name,
            power=float(power_w),
            metering_device=device,
            critical_power_w=max(float(power_w) * 2.5, 2500.0),
            id=self.storage.next_appliance_id(),
        )
        appliance.state_machine.turn_on()
        if power_w > 0:
            appliance.state_machine.start_operation()
        room.add_appliance(appliance)
        self.storage.save(
            Event(
                event_type="device_registered",
                timestamp=datetime.now(LOCAL_TZ),
                description=f"Зарегистрировано устройство «{clean_name}» ({clean_serial}) в «{room.name}»",
                user_login=user.login,
            )
        )
        return appliance

    def change_appliance_state(self, user: User, appliance_id: int, action: str) -> Appliance:
        self.access_control.require_permission(user, "manage_devices")
        home = user.home
        if home is None:
            raise ValidationError("Дом не найден.")
        target_appliance: Optional[Appliance] = None
        target_room: Optional[Room] = None
        for room in home.rooms:
            for appliance in room.appliances:
                if appliance.id == appliance_id:
                    target_appliance = appliance
                    target_room = room
                    break
        if target_appliance is None or target_room is None:
            raise ValidationError("Устройство не найдено.")

        target_appliance.display_status = None
        try:
            if action == "enable":
                target_appliance.state_machine.turn_on()
                if target_appliance.metering_device:
                    target_appliance.metering_device.active = True
            elif action == "disable":
                target_appliance.state_machine.turn_off()
                if target_appliance.metering_device:
                    target_appliance.metering_device.active = False
            elif action == "start":
                target_appliance.state_machine.start_operation()
            elif action == "flexible":
                target_appliance.state_machine.switch_to_off_peak()
            elif action == "finish_flexible":
                target_appliance.state_machine.finish_flexible_cycle()
            elif action == "finish":
                target_appliance.state_machine.finish_normal_operation()
            elif action == "anomaly":
                target_appliance.state_machine.trigger_anomaly(
                    critical_threshold_exceeded=True,
                    notify_callback=lambda: self.notifications.send_notification(
                        f"Аварийное обесточивание прибора «{target_appliance.name}» ({target_room.name}).",
                        notification_type="anomaly",
                    ),
                )
                anomaly = Anomaly(
                    anomaly_type="Скачок мощности",
                    magnitude=45.0,
                    timestamp=datetime.now(LOCAL_TZ),
                    location=f"линии {target_room.name.lower()}",
                )
                self.storage.save(anomaly)
            elif action == "reset":
                target_appliance.state_machine.clear_fault()
            elif action == "retire":
                target_appliance.state_machine.retire()
            else:
                raise ValidationError(f"Неизвестное действие: {action}")
        except ValueError as err:
            raise ValidationError(str(err)) from err

        self.storage.save(
            Event(
                event_type="appliance_state_changed",
                timestamp=datetime.now(LOCAL_TZ),
                description=f"{target_appliance.name}: {target_appliance.state_machine.current_state.value}",
                user_login=user.login,
            )
        )
        return target_appliance
