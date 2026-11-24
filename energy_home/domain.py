"""Domain Core — 1:1 implementation of Base Class Diagram 4.1 (page 13) and ER diagrams."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    LOCAL_TZ: Any = ZoneInfo("Europe/Minsk")
except (ImportError, ZoneInfoNotFoundError):
    LOCAL_TZ = timezone(timedelta(hours=3), name="Europe/Minsk")


class DomainError(Exception):
    """Base domain exception."""


class ValidationError(DomainError):
    """Raised when input data or a domain invariant is invalid."""


class AccessDeniedError(DomainError):
    """Raised on authentication failure or RBAC permission denial."""


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 (260,000 iterations)."""
    if not isinstance(password, str) or len(password) < 6:
        raise ValidationError("Пароль должен содержать не менее 6 символов.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000, dklen=32)
    return f"pbkdf2_sha256$260000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a PBKDF2-SHA256 hash in constant time."""
    parts = (stored_hash or "").split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    _, iterations_str, salt_hex, expected_hex = parts
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), salt, iterations, dklen=32)
    return hmac.compare_digest(actual, expected)


# ---------------------------------------------------------------------------
# Base Class Diagram 4.1 — Package "Domain Core" (page 13)
# ---------------------------------------------------------------------------


class Role(str, Enum):
    """UML Enum `Роль` (4.1, page 13): OWNER (ВЛАДЕЛЕЦ), FAMILY_MEMBER (ЧЛЕН_СЕМЬИ)."""

    OWNER = "OWNER"
    FAMILY_MEMBER = "FAMILY_MEMBER"

    @property
    def display_label(self) -> str:
        return "Владелец дома" if self == Role.OWNER else "Член семьи"


@dataclass
class Tariff:
    """UML Class `Тариф` (4.1, page 13): day_rate (стоимостьДень), night_rate (стоимостьНочь)."""

    day_rate: float = 5.50
    night_rate: float = 2.10
    day_start: int = 7
    night_start: int = 23

    def rate_for_time(self, timestamp: datetime) -> float:
        hour = timestamp.astimezone(LOCAL_TZ).hour if timestamp.tzinfo else timestamp.hour
        if self.day_start <= hour < self.night_start:
            return self.day_rate
        return self.night_rate


@dataclass
class Reading:
    """UML Class `Показание` (4.1, page 13): value (значение), timestamp (время), tariff (Тариф)."""

    value: float
    timestamp: datetime
    tariff: Optional[Tariff] = None
    power_w: float = 0.0
    device_serial: str = ""
    room_name: str = ""
    appliance_name: str = ""

    def cost(self) -> float:
        if self.tariff is None:
            return 0.0
        return self.value * self.tariff.rate_for_time(self.timestamp)


@dataclass
class MeteringDevice:
    """UML Class `Устройство учета` (4.1, page 13): device_type, serial_number, read_measurement()."""

    device_type: str
    serial_number: str
    tariff: Optional[Tariff] = None
    readings: list[Reading] = field(default_factory=list)
    current_power_w: float = 0.0
    active: bool = True

    def read_measurement(
        self,
        value: Optional[float] = None,
        timestamp: Optional[datetime] = None,
        power_w: Optional[float] = None,
    ) -> Reading:
        """UML method `+считатьПоказание(): Показание`."""
        moment = timestamp or datetime.now(LOCAL_TZ)
        power = self.current_power_w if power_w is None else float(power_w)
        if value is None:
            value = round((power / 1000.0) if self.active else 0.0, 4)
        reading = Reading(
            value=float(value),
            timestamp=moment,
            tariff=self.tariff,
            power_w=power,
            device_serial=self.serial_number,
        )
        self.readings.append(reading)
        return reading


@dataclass
class Appliance:
    """UML Class `Прибор` (4.1, page 13): name, power, 1..1 -> MeteringDevice."""

    name: str
    power: float
    metering_device: Optional[MeteringDevice] = None
    critical_power_w: float = 3500.0
    flexible_load: bool = False
    essential: bool = False
    display_status: Optional[str] = None
    id: int = 0
    state_machine: Any = None

    def __post_init__(self) -> None:
        from energy_home.state_machine import DeviceStateMachine

        if self.state_machine is None:
            self.state_machine = DeviceStateMachine()

    def current_power_w(self) -> float:
        from energy_home.state_machine import DeviceState

        if self.state_machine.outer_state != DeviceState.ON:
            return 0.0
        if self.metering_device is not None and self.metering_device.current_power_w > 0:
            return self.metering_device.current_power_w
        return self.power

    def tree_label(self) -> str:
        """Format appliance row for the rooms tree per Mockup 5.3.3 (page 19)."""
        from energy_home.state_machine import DeviceState

        if self.display_status:
            return f"{self.name} ({self.display_status})"
        outer = self.state_machine.outer_state
        inner = self.state_machine.inner_state
        if outer == DeviceState.OFF:
            return f"{self.name} (не активно)"
        if outer == DeviceState.EMERGENCY_MODE:
            return f"{self.name} (авария: отключено)"
        if outer == DeviceState.RETIRED:
            return f"{self.name} (списан)"
        if inner == DeviceState.FLEXIBLE_LOAD:
            return f"{self.name} (авторежим)"
        if self.power > 0:
            return f"{self.name} (потребление: {int(round(self.current_power_w()))} Вт)"
        return f"{self.name} (активно)"


@dataclass
class Room:
    """UML Class `Комната` (4.1, page 13): name, composition 1..* -> Appliance."""

    name: str
    appliances: list[Appliance] = field(default_factory=list)
    id: int = 0
    collapsed_by_default: bool = False

    def add_appliance(self, appliance: Appliance) -> None:
        self.appliances.append(appliance)


@dataclass
class Home:
    """UML Class `Дом / Квартира` (4.1, page 13): address, get_total_consumption(), 1..* -> Room."""

    address: str
    rooms: list[Room] = field(default_factory=list)
    home_type: str = "apartment"
    base_daily_consumption_kwh: float = 0.0
    base_power_kw: float = 0.0

    def get_total_consumption(self) -> float:
        """UML method `+получитьОбщееПотребление(): Double`."""
        total = self.base_daily_consumption_kwh
        for room in self.rooms:
            for appliance in room.appliances:
                if appliance.metering_device is not None:
                    total += sum(r.value for r in appliance.metering_device.readings)
        return round(total, 2)

    def current_power_kw(self) -> float:
        """Current aggregate power across active appliances in kW."""
        total_w = 0.0
        for room in self.rooms:
            for appliance in room.appliances:
                total_w += appliance.current_power_w()
        if total_w == 0.0 and self.base_power_kw > 0.0:
            return round(self.base_power_kw, 2)
        return round(self.base_power_kw + (total_w / 1000.0), 1)

    def add_room(self, room: Room) -> None:
        self.rooms.append(room)


@dataclass
class Event:
    """UML Class `Событие` (4.1, page 13): event_type (тип), timestamp (дата)."""

    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(LOCAL_TZ))
    description: str = ""
    user_login: Optional[str] = None


@dataclass
class User:
    """UML Class `Пользователь` (4.1, page 13): login, password, authenticate(), links to Role/Event/Home."""

    login: str
    password: str
    role: Role = Role.OWNER
    home: Optional[Home] = None
    name: str = ""
    registered_at: datetime = field(default_factory=lambda: datetime.now(LOCAL_TZ))
    events: list[Event] = field(default_factory=list)
    active: bool = True
    _candidate_password: Optional[str] = field(default=None, repr=False, compare=False)

    def authenticate(self, candidate_password: Optional[str] = None) -> bool:
        """UML method `+авторизоваться(): Boolean`."""
        if not self.active:
            return False
        candidate = self._candidate_password if candidate_password is None else candidate_password
        if candidate is None:
            return bool(self.login and self.password)
        if self.password.startswith("pbkdf2_sha256$"):
            ok = verify_password(candidate, self.password)
        else:
            ok = hmac.compare_digest(candidate, self.password)
        if ok:
            self.events.append(
                Event(
                    event_type="login",
                    timestamp=datetime.now(LOCAL_TZ),
                    description="Успешная авторизация",
                    user_login=self.login,
                )
            )
        return ok


# ---------------------------------------------------------------------------
# Supporting domain entities from ER diagrams (pp. 4–8), 4.1, and 4.2.3 (p. 15)
# ---------------------------------------------------------------------------


@dataclass
class Anomaly:
    """UML Class `Аномалия` (4.2.3, page 15; ER-3/ER-5): anomaly_type, magnitude, timestamp, describe()."""

    anomaly_type: str
    magnitude: float
    timestamp: datetime
    location: str = "линии кухни"
    custom_time_label: Optional[str] = None

    def describe(self) -> str:
        """UML method `+описать(): String`."""
        sign = "+" if self.magnitude >= 0 else ""
        time_str = self.custom_time_label or self.timestamp.strftime("%d.%m.%Y %H:%M")
        return f"{self.anomaly_type} на {self.location} ({sign}{int(round(self.magnitude))}%)\n{time_str}."


@dataclass
class Limit:
    """ER entity `Лимит` (ER-2/ER-5, Mockups 2 and 5)."""

    monthly_budget_byn: float = 5000.0
    daily_limit_kwh: float = 80.0
    notify_at_80: bool = True
    period: str = "month"


@dataclass
class Notification:
    """ER entity `Уведомление` (ER-3/ER-5): message, notification_type, sent_at."""

    message: str
    notification_type: str = "info"
    sent_at: datetime = field(default_factory=lambda: datetime.now(LOCAL_TZ))
    is_read: bool = False


@dataclass
class Chart:
    """Return type `Chart` of ReportingSubsystem.build_chart() (4.1, page 13)."""

    title: str
    subtitle: str
    period: str
    labels: list[str]
    values: list[float]
    y_step: float = 5.0
    y_max: float = 25.0


@dataclass
class Report:
    """Return type `Отчет` of ReportingSubsystem.generate_report() (4.1, page 13)."""

    period: str
    generated_at: datetime
    total_consumption_kwh: float
    total_cost_byn: float
    chart: Chart
    anomalies: list[Anomaly] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
