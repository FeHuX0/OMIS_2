import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .domain import ValidationError
from .services import number


class SmartHomeAdapter(Protocol):
    def power_w(self, entity_id: str) -> float: ...

    def switch(self, entity_id: str, enabled: bool) -> None: ...


class HomeAssistantAdapter:
    """Не хранит токен в БД. Вызовы выполняются только по явному запросу пользователя."""

    def __init__(self, base_url: str, token: str):
        if not base_url.startswith(("http://", "https://")) or not token.strip():
            raise ValidationError("Укажите HTTP(S)-адрес Home Assistant и токен доступа.")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, path, payload=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.load(response)
        except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
            raise ValidationError("Home Assistant недоступен или вернул неверный ответ.") from exc

    @staticmethod
    def _entity(entity_id):
        if not entity_id or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789_." for c in entity_id
        ):
            raise ValidationError("Некорректный идентификатор сущности Home Assistant.")
        return entity_id

    def power_w(self, entity_id):
        data = self._request("/api/states/" + self._entity(entity_id))
        unit = data.get("attributes", {}).get("unit_of_measurement")
        if unit not in ("W", "kW"):
            raise ValidationError("Датчик должен сообщать мощность в W или kW.")
        return number(data.get("state"), "Мощность датчика", 0, 10_000_000) * (
            1000 if unit == "kW" else 1
        )

    def switch(self, entity_id, enabled):
        entity_id = self._entity(entity_id)
        if not entity_id.startswith("switch."):
            raise ValidationError("Для управления нужна сущность switch.*.")
        self._request(
            "/api/services/switch/turn_" + ("on" if enabled else "off"), {"entity_id": entity_id}
        )


@dataclass(frozen=True)
class VoiceCommand:
    text: str
    intent: str


class VoiceAssistant:
    """Обработчик распознанного текста. Источник речи подключается отдельным адаптером."""

    def __init__(self, energy, reports, automation):
        self.energy, self.reports, self.automation = energy, reports, automation

    def recognize(self, text):
        cleaned = text.strip().casefold()
        commands = {
            "текущее потребление": "current",
            "отчёт за месяц": "month",
            "отчет за месяц": "month",
            "эко-режим": "eco",
            "отпуск": "vacation",
            "ночной тариф": "night_tariff",
        }
        if cleaned not in commands:
            raise ValidationError(
                "Команды: текущее потребление; отчёт за месяц; эко-режим; отпуск; ночной тариф."
            )
        return VoiceCommand(text, commands[cleaned])

    def execute(self, session, command):
        self.energy.access.require(session)
        if command.intent == "current":
            dashboard = self.reports.dashboard(session)
            return f"Сегодня израсходовано {dashboard['daily_kwh']:.2f} киловатт-часа."
        if command.intent == "month":
            report = self.reports.build(session)
            return f"За месяц: {report.energy_kwh:.2f} киловатт-часа, {report.cost:.2f} BYN."
        changed = self.automation.apply(session, command.intent)
        return f"Сценарий применён. Изменено приборов: {changed}."


class BuildingService:
    """Изолированный учёт общих зон: квартиры и правила распределения долей."""

    def __init__(self, db, access):
        self.db, self.access = db, access

    def apartments(self, session):
        user = self.access.require(session)
        return self.db.all(
            "SELECT a.* FROM building_apartments a JOIN buildings b ON b.id=a.building_id "
            "WHERE b.home_id=? ORDER BY a.id",
            (user["home_id"],),
        )

    def add_apartment(self, session, name, area, residents, own_kwh):
        user = self.access.require(session, owner=True)
        area = number(area, "Площадь", 0.1)
        own_kwh = number(own_kwh, "Потребление квартиры")
        if type(residents) is not int or not 1 <= residents <= 100:
            raise ValidationError("Число жильцов: от 1 до 100.")
        if not name.strip() or len(name) > 100:
            raise ValidationError("Укажите название квартиры длиной до 100 символов.")
        import sqlite3

        try:
            with self.db.transaction():
                self.db.execute(
                    "INSERT OR IGNORE INTO buildings(home_id,name) VALUES(?,?)",
                    (user["home_id"], "Многоквартирный дом"),
                )
                building = self.db.one(
                    "SELECT id FROM buildings WHERE home_id=?", (user["home_id"],)
                )
                self.db.execute(
                    "INSERT INTO building_apartments(building_id,name,area,residents,own_kwh) "
                    "VALUES(?,?,?,?,?)",
                    (building["id"], name.strip(), area, residents, own_kwh),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Квартира с таким названием уже существует.") from exc

    def distribute(self, session, common_kwh, method="area"):
        apartments = self.apartments(session)
        common_kwh = number(common_kwh, "Расход общих зон")
        if method not in ("area", "residents", "equal"):
            raise ValidationError("Правила: по площади, по числу жильцов или поровну.")
        if not apartments:
            return []
        weights = [1 if method == "equal" else a[method] for a in apartments]
        total = sum(weights)
        # Рейтинг сравнивает удельное потребление (кВт·ч/м²) с домом, а не размер квартиры.
        average = sum(a["own_kwh"] for a in apartments) / sum(a["area"] for a in apartments)
        result = []
        for apartment, weight in zip(apartments, weights, strict=True):
            share = common_kwh * weight / total
            intensity = apartment["own_kwh"] / apartment["area"]
            result.append(
                {
                    **apartment,
                    "common_kwh": share,
                    "total_kwh": apartment["own_kwh"] + share,
                    "efficiency_percent": (1 - intensity / average) * 100 if average else 0,
                }
            )
        return result
