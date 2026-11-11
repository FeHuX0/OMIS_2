import csv
import queue
import random
from datetime import datetime, timedelta
from pathlib import Path

from .domain import DeviceState, DomainError, ReadingPacket, ValidationError, now


class SensorGateway:
    """Потокобезопасный вход для адаптеров. Обработка очереди — в потоке SQLite/Qt."""

    def __init__(self, service):
        self.service = service
        self.pending = queue.SimpleQueue()

    def submit(self, session, packet):
        self.pending.put((session, packet))

    def drain(self, maximum=100):
        errors = []
        count = 0
        while not self.pending.empty() and count < maximum:
            session, packet = self.pending.get_nowait()
            try:
                self.service.ingest(session, packet)
            except DomainError as exc:
                errors.append(str(exc))
            count += 1
        return count, errors


def import_csv(service, session, path: str | Path):
    service.access.require(session, owner=True)
    accepted = 0
    errors = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(4096)
        stream.seek(0)
        delimiter = ";" if sample.splitlines() and ";" in sample.splitlines()[0] else ","
        reader = csv.DictReader(stream, delimiter=delimiter)
        required = {"serial", "measured_at", "power_w", "energy_kwh", "interval_seconds"}
        if not required <= set(reader.fieldnames or []):
            raise ValidationError("В CSV нужны столбцы: " + ", ".join(sorted(required)))
        devices = {d["serial"].casefold(): d for d in service.devices(session)}
        for line, row in enumerate(reader, 2):
            try:
                device = devices.get(row["serial"].strip().casefold())
                if not device:
                    raise ValidationError("Серийный номер не зарегистрирован в вашем доме.")
                packet = ReadingPacket(
                    device["id"],
                    datetime.fromisoformat(row["measured_at"]),
                    float(row["power_w"]),
                    float(row["energy_kwh"]),
                    int(row["interval_seconds"]),
                )
                service.ingest(session, packet)
                accepted += 1
            except (DomainError, ValueError, TypeError, AttributeError) as exc:
                message = f"Строка {line}: {exc}"
                errors.append(message)
                # Ошибки разбора тоже входят в журнал, но не блокируют корректные строки.
                user = service.access.require(session, owner=True)
                with service.db.transaction():
                    service.db.event(user["home_id"], "import_error", message)
    return accepted, errors


class Simulator:
    """Локальный источник данных, не выдающий себя за подключённое оборудование."""

    def __init__(self, service, gateway):
        self.service, self.gateway = service, gateway
        self.random = random.Random()

    def tick(self, session):
        current = now()
        for device in self.service.devices(session):
            last = self.service.db.one(
                "SELECT measured_at,interval_seconds FROM readings WHERE device_id=? "
                "ORDER BY measured_at DESC LIMIT 1",
                (device["id"],),
            )
            start = current - timedelta(seconds=10)
            if last:
                last_end = datetime.fromisoformat(last["measured_at"]) + timedelta(
                    seconds=last["interval_seconds"]
                )
                start = max(start, last_end)
            duration = int((current - start).total_seconds())
            if duration < 1:
                continue
            power = 0.0
            if device["state"] in (DeviceState.IDLE, DeviceState.RUNNING):
                factor = 0.25 if 7 <= current.hour < 23 or device["essential"] else 0.015
                if device["flexible"]:
                    factor = 0.65 if current.hour in (19, 20) else 0.005
                if device["state"] == DeviceState.IDLE and not device["essential"]:
                    factor = 0.005
                power = device["rated_w"] * factor * self.random.uniform(0.8, 1.2)
            self.gateway.submit(
                session,
                ReadingPacket(device["id"], start, power, power / 1000 * duration / 3600, duration),
            )
