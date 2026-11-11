from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Europe/Minsk")


def now() -> datetime:
    return datetime.now(LOCAL_TZ)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValidationError("Укажите часовой пояс времени показания.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class DomainError(Exception):
    """Ошибка, которую можно безопасно показать пользователю."""


class ValidationError(DomainError):
    pass


class AccessDenied(DomainError):
    pass


class Role(StrEnum):
    OWNER = "owner"
    FAMILY = "family"


class DeviceState(StrEnum):
    OFF = "off"
    IDLE = "idle"
    RUNNING = "running"
    FLEXIBLE = "flexible"
    EMERGENCY = "emergency"
    RETIRED = "retired"


STATE_LABELS = {
    DeviceState.OFF: "Выключен",
    DeviceState.IDLE: "Ожидание активности",
    DeviceState.RUNNING: "Штатный режим",
    DeviceState.FLEXIBLE: "Ожидание ночного тарифа",
    DeviceState.EMERGENCY: "Аварийное обесточивание",
    DeviceState.RETIRED: "Списан",
}

# Явный конечный автомат: авария снимается только через выключенное состояние.
TRANSITIONS = {
    DeviceState.OFF: {"enable": DeviceState.IDLE, "retire": DeviceState.RETIRED},
    DeviceState.IDLE: {
        "start": DeviceState.RUNNING,
        "disable": DeviceState.OFF,
        "schedule": DeviceState.FLEXIBLE,
        "anomaly": DeviceState.EMERGENCY,
        "retire": DeviceState.RETIRED,
    },
    DeviceState.RUNNING: {
        "finish": DeviceState.IDLE,
        "disable": DeviceState.OFF,
        "schedule": DeviceState.FLEXIBLE,
        "anomaly": DeviceState.EMERGENCY,
        "retire": DeviceState.RETIRED,
    },
    DeviceState.FLEXIBLE: {
        "night": DeviceState.RUNNING,
        "disable": DeviceState.OFF,
        "anomaly": DeviceState.EMERGENCY,
        "retire": DeviceState.RETIRED,
    },
    DeviceState.EMERGENCY: {"reset": DeviceState.OFF},
    DeviceState.RETIRED: {},
}


def transition(state: str, action: str) -> DeviceState:
    result = TRANSITIONS[DeviceState(state)].get(action)
    if result is None:
        raise ValidationError(f"Действие «{action}» недоступно: {STATE_LABELS[state]}.")
    return result


class Screen(StrEnum):
    LOGIN = "login"
    HOME = "home"
    DEVICES = "devices"
    REPORTS = "reports"
    SETTINGS = "settings"


class Navigation:
    def __init__(self):
        self.current = Screen.LOGIN

    def go(self, target: Screen, role: Role | None = None):
        allowed = {
            Screen.LOGIN: {Screen.HOME},
            Screen.HOME: {Screen.LOGIN, Screen.DEVICES, Screen.REPORTS, Screen.SETTINGS},
            Screen.DEVICES: {Screen.HOME, Screen.LOGIN},
            Screen.REPORTS: {Screen.HOME, Screen.LOGIN},
            Screen.SETTINGS: {Screen.HOME, Screen.LOGIN},
        }
        if target == Screen.SETTINGS and role != Role.OWNER:
            raise AccessDenied("Настройки доступны только владельцу.")
        if target not in allowed[self.current]:
            raise ValidationError("Этот переход между окнами недоступен.")
        self.current = target


@dataclass(frozen=True)
class Session:
    token: str


@dataclass(frozen=True)
class ReadingPacket:
    device_id: int
    measured_at: datetime
    power_w: float
    energy_kwh: float
    interval_seconds: int = 60


@dataclass(frozen=True)
class Report:
    start: datetime
    end: datetime
    energy_kwh: float
    cost: float
    previous_kwh: float
    change_percent: float | None
    series: list[tuple[str, float]]
    devices: list[dict]
    anomalies: list[dict]
    recommendations: list[str]
