"""Three extension directions — 1:1 implementation of Class Diagrams 4.2.1, 4.2.2, 4.2.3 (pp. 14–15)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from energy_home.domain import (
    LOCAL_TZ,
    Anomaly,
    Appliance,
    Event,
    Home,
    Tariff,
    ValidationError,
)
from energy_home.state_machine import DeviceState
from energy_home.subsystems import (
    AnalysisSubsystem,
    ApplicationInterface,
    NotificationSubsystem,
    ReportingSubsystem,
)


# ===========================================================================
# 4.2.1 Extension 1: Voice Assistant & Smart Home Integration (page 14)
# ===========================================================================


@dataclass
class VoiceCommand:
    """UML `ГолосоваяКоманда` (4.2.1): text (текст), intent (намерение)."""

    text: str
    intent: str


class SmartHomeAdapter(ABC):
    """Abstract UML `АдаптерУмногоДома` (4.2.1): send_command(device, cmd), get_events()."""

    @abstractmethod
    def send_command(self, device: Appliance | str, cmd: str) -> None:
        """UML method `+передатьКоманду(устр, cmd): void`."""

    @abstractmethod
    def get_events(self) -> Event:
        """UML method `+получитьСобытия(): Event`."""


class YandexAliceAdapter(SmartHomeAdapter):
    """UML `АдаптерЯндексАлиса` (4.2.1)."""

    def __init__(self, skill_id: str = "energy-home-alice") -> None:
        self.skill_id = skill_id
        self.command_history: list[tuple[str, str]] = []

    def send_command(self, device: Appliance | str, cmd: str) -> None:
        name = device.name if isinstance(device, Appliance) else str(device)
        self.command_history.append((name, cmd))

    def get_events(self) -> Event:
        return Event(
            event_type="yandex_alice_event",
            timestamp=datetime.now(LOCAL_TZ),
            description=f"Навык {self.skill_id} активен",
        )


class GoogleHomeAdapter(SmartHomeAdapter):
    """UML `АдаптерGoogleHome` (4.2.1)."""

    def __init__(self, project_id: str = "energy-home-google") -> None:
        self.project_id = project_id
        self.command_history: list[tuple[str, str]] = []

    def send_command(self, device: Appliance | str, cmd: str) -> None:
        name = device.name if isinstance(device, Appliance) else str(device)
        self.command_history.append((name, cmd))

    def get_events(self) -> Event:
        return Event(
            event_type="google_home_event",
            timestamp=datetime.now(LOCAL_TZ),
            description=f"Проект {self.project_id} готов",
        )


class HomeAssistantAdapter(SmartHomeAdapter):
    """UML `АдаптерHomeAssistant` (4.2.1)."""

    def __init__(self, url: str = "http://homeassistant.local:8123") -> None:
        self.url = url
        self.command_history: list[tuple[str, str]] = []

    def send_command(self, device: Appliance | str, cmd: str) -> None:
        name = device.name if isinstance(device, Appliance) else str(device)
        self.command_history.append((name, cmd))

    def get_events(self) -> Event:
        return Event(
            event_type="home_assistant_event",
            timestamp=datetime.now(LOCAL_TZ),
            description=f"Шлюз {self.url} готов",
        )


class VoiceAssistant:
    """<<control>> UML `ГолосовойАссистент` (4.2.1): recognize_command(audio), speak_response(text)."""

    INTENTS: dict[str, str] = {
        "текущее потребление": "consumption",
        "отчет за месяц": "report",
        "отчёт за месяц": "report",
        "выключить приборы": "turn_off",
        "эко-режим": "eco",
        "ночной тариф": "night_tariff",
    }

    def __init__(
        self,
        adapter: Optional[SmartHomeAdapter] = None,
        app_interface: Optional[ApplicationInterface] = None,
        analysis: Optional[AnalysisSubsystem] = None,
        reporting: Optional[ReportingSubsystem] = None,
        notifications: Optional[NotificationSubsystem] = None,
    ) -> None:
        self.adapter = adapter or HomeAssistantAdapter()
        self.app_interface = app_interface
        self.analysis = analysis
        self.reporting = reporting
        self.notifications = notifications
        self.last_response: str = ""

    def recognize_command(self, audio: str | bytes) -> VoiceCommand:
        """UML method `+распознатьКоманду(аудио): ГолосоваяКоманда`."""
        text = audio.decode("utf-8", errors="ignore") if isinstance(audio, bytes) else str(audio)
        key = text.strip().lower()
        intent = self.INTENTS.get(key, "unknown")
        return VoiceCommand(text=text.strip(), intent=intent)

    def speak_response(self, text: str) -> None:
        """UML method `+озвучитьОтвет(текст): void`."""
        self.last_response = text


# ===========================================================================
# 4.2.2 Extension 2: Apartment Building & Common Zones (page 14)
# ===========================================================================


@dataclass
class CommonZone:
    """UML `ОбщаяЗона` (4.2.2): zone_type (тип), get_consumption()."""

    zone_type: str
    consumption_kwh: float = 0.0

    def get_consumption(self) -> float:
        """UML method `+получитьРасход(): Double`."""
        return round(float(self.consumption_kwh), 2)


@dataclass
class DistributionRule:
    """UML `ПравилоРаспределения` (4.2.2): rule_type (тип), distribute(consumption)."""

    rule_type: str = "by_area"  # "by_area" | "by_residents" | "equal"

    def distribute(
        self,
        consumption: float,
        apartment_weights: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """UML method `+распределить(расход): Map`."""
        if consumption < 0:
            raise ValidationError("Общедомовой расход не может быть отрицательным.")
        if not apartment_weights:
            return {}
        if self.rule_type == "equal":
            share = round(consumption / len(apartment_weights), 2)
            return {apt: share for apt in apartment_weights}
        total_weight = sum(max(0.0, float(w)) for w in apartment_weights.values())
        if total_weight <= 0:
            raise ValidationError("Сумма весов распределения должна быть больше нуля.")
        return {
            apt: round(consumption * (float(weight) / total_weight), 2)
            for apt, weight in apartment_weights.items()
        }


@dataclass
class EnergyEfficiencyRating:
    """UML `РейтингЭнергоэффективности` (4.2.2): score (балл), compare_with_average()."""

    score: float = 50.0
    apartment_consumption: float = 0.0
    building_average: float = 0.0

    def compare_with_average(self) -> float:
        """UML method `+сравнитьСоСредним(): Double`."""
        if self.building_average <= 0:
            return 0.0
        percent = (1.0 - self.apartment_consumption / self.building_average) * 100.0
        self.score = max(0.0, min(100.0, round(50.0 + percent, 1)))
        return round(percent, 1)


@dataclass
class ApartmentBuilding:
    """UML `МногоквартирныйДом` (4.2.2): address (адрес), get_total_consumption()."""

    address: str
    common_zones: list[CommonZone] = field(default_factory=list)
    apartments: list[Home] = field(default_factory=list)
    distribution_rule: DistributionRule = field(default_factory=DistributionRule)

    def get_total_consumption(self) -> float:
        """UML method `+получитьОбщийРасход(): Double`."""
        common_total = sum(z.get_consumption() for z in self.common_zones)
        apartments_total = sum(apt.get_total_consumption() for apt in self.apartments)
        return round(common_total + apartments_total, 2)


# ===========================================================================
# 4.2.3 Extension 3: Smart Automatic Appliance Control (page 15)
# ===========================================================================


@dataclass
class Rule:
    """UML `Правило` (4.2.3): condition (условие), action (действие)."""

    condition: str
    action: str


class DeviceControlModule:
    """<<control>> UML `МодульУправленияУстройствами` (4.2.3): turn_off(appliance), turn_on(appliance)."""

    def turn_off(self, appliance: Appliance) -> None:
        """UML method `+отключить(прибор): void`."""
        if appliance.state_machine.outer_state == DeviceState.ON:
            appliance.state_machine.turn_off()
        if appliance.metering_device:
            appliance.metering_device.active = False

    def turn_on(self, appliance: Appliance) -> None:
        """UML method `+включить(прибор): void`."""
        if appliance.state_machine.outer_state == DeviceState.OFF:
            appliance.state_machine.turn_on()
        if appliance.state_machine.inner_state == DeviceState.IDLE_WAITING:
            appliance.state_machine.start_operation()
        if appliance.metering_device:
            appliance.metering_device.active = True


@dataclass
class Scenario:
    """UML `Сценарий` (4.2.3): name (имя), execute()."""

    name: str
    appliances: list[Appliance] = field(default_factory=list)
    control_module: DeviceControlModule = field(default_factory=DeviceControlModule)

    def execute(self) -> None:
        """UML method `+выполнить(): void`."""
        normalized = self.name.strip().lower()
        for appliance in self.appliances:
            if appliance.essential or appliance.state_machine.outer_state in (
                DeviceState.EMERGENCY_MODE,
                DeviceState.RETIRED,
            ):
                continue
            if normalized in ("отпуск", "vacation"):
                self.control_module.turn_off(appliance)
            elif normalized in ("эко-режим", "эко", "eco"):
                if appliance.flexible_load and appliance.state_machine.outer_state == DeviceState.ON:
                    appliance.state_machine.switch_to_off_peak()
                elif appliance.state_machine.inner_state == DeviceState.IDLE_WAITING:
                    self.control_module.turn_off(appliance)
            elif normalized in ("ночной тариф", "night_tariff"):
                if appliance.flexible_load and appliance.state_machine.outer_state == DeviceState.ON:
                    appliance.state_machine.switch_to_off_peak()


class LoadScheduler:
    """<<control>> UML `ПланировщикНагрузки` (4.2.3): reschedule_load(appliance, target_time)."""

    def __init__(
        self,
        control_module: Optional[DeviceControlModule] = None,
        tariff: Optional[Tariff] = None,
    ) -> None:
        self.control_module = control_module or DeviceControlModule()
        self.tariff = tariff or Tariff()
        self.schedule: list[tuple[str, str]] = []

    def reschedule_load(self, appliance: Appliance, target_time: str | datetime) -> None:
        """UML method `+перенестиНагрузку(прибор, время): void`."""
        if appliance.state_machine.outer_state == DeviceState.OFF:
            self.control_module.turn_on(appliance)
        if appliance.state_machine.outer_state == DeviceState.ON:
            appliance.state_machine.switch_to_off_peak()
        label = target_time.isoformat() if isinstance(target_time, datetime) else str(target_time)
        self.schedule.append((appliance.name, label))


class RuleSubsystem:
    """<<control>> UML `ПодсистемаПравил` (4.2.3): check_rule(event)."""

    def __init__(
        self,
        rules: Optional[list[Rule]] = None,
        scenarios: Optional[list[Scenario]] = None,
        analysis: Optional[AnalysisSubsystem] = None,
    ) -> None:
        self.rules: list[Rule] = rules if rules is not None else [
            Rule(condition="аномалия", action="аварийное обесточивание линии"),
            Rule(condition="ночной тариф", action="перенос гибкой нагрузки"),
        ]
        self.scenarios: list[Scenario] = scenarios if scenarios is not None else []
        self.analysis = analysis

    def check_rule(self, event: Event | Anomaly | str) -> bool:
        """UML method `+проверитьПравило(событие): Boolean`."""
        if isinstance(event, Anomaly):
            text = event.anomaly_type.lower()
        elif isinstance(event, Event):
            text = f"{event.event_type} {event.description}".lower()
        else:
            text = str(event).lower()
        return any(rule.condition.lower() in text for rule in self.rules)
