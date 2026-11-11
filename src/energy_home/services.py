import math
import sqlite3
from datetime import datetime, timedelta

from .auth import AccessControl
from .domain import (
    LOCAL_TZ,
    DeviceState,
    ReadingPacket,
    ValidationError,
    now,
    stamp,
    transition,
)
from .storage import Database

DEVICE_SELECT = """
SELECT d.*, a.name, a.rated_w, a.flexible, a.essential, a.room_id,
       r.name AS room, r.home_id
FROM devices d JOIN appliances a ON a.id=d.appliance_id JOIN rooms r ON r.id=a.room_id
"""


def number(value, name, minimum=0, maximum=1_000_000):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name}: введите число.") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValidationError(f"{name}: допустимо от {minimum:g} до {maximum:g}.")
    return result


class NotificationService:
    def __init__(self, db, access):
        self.db, self.access = db, access

    def send(self, home_id, kind, message, key=None):
        self.db.execute(
            "INSERT OR IGNORE INTO notifications(home_id,kind,message,created_at,dedupe_key) "
            "VALUES(?,?,?,?,?)",
            (home_id, kind, message, stamp(now()), key),
        )

    def list(self, session, limit=100):
        user = self.access.require(session)
        return self.db.all(
            "SELECT n.*, (nr.user_id IS NOT NULL) AS is_read FROM notifications n "
            "LEFT JOIN notification_reads nr ON nr.notification_id=n.id AND nr.user_id=? "
            "WHERE n.home_id=? ORDER BY n.id DESC LIMIT ?",
            (user["id"], user["home_id"], limit),
        )

    def mark_read(self, session):
        user = self.access.require(session)
        with self.db.transaction():
            self.db.execute(
                "INSERT OR IGNORE INTO notification_reads(notification_id,user_id) "
                "SELECT id,? FROM notifications WHERE home_id=?",
                (user["id"], user["home_id"]),
            )


class EnergyService:
    """Сбор, валидация и управление; транзакция охватывает весь цикл обработки пакета."""

    def __init__(self, db: Database, access: AccessControl):
        self.db, self.access = db, access
        self.notifications = NotificationService(db, access)

    def home(self, session):
        user = self.access.require(session)
        return self.db.one("SELECT * FROM homes WHERE id=?", (user["home_id"],))

    def rooms(self, session):
        user = self.access.require(session)
        return self.db.all("SELECT * FROM rooms WHERE home_id=? ORDER BY id", (user["home_id"],))

    def add_room(self, session, name):
        user = self.access.require(session, owner=True)
        name = name.strip()
        if not name or len(name) > 100:
            raise ValidationError("Название комнаты должно содержать от 1 до 100 символов.")
        try:
            with self.db.transaction():
                result = self.db.execute(
                    "INSERT INTO rooms(home_id,name) VALUES(?,?)", (user["home_id"], name)
                ).lastrowid
                self.db.event(user["home_id"], "room_added", f"Добавлена комната: {name}")
                return result
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Комната с таким названием уже существует.") from exc

    def devices(self, session, include_retired=False):
        user = self.access.require(session)
        devices = self.db.all(
            DEVICE_SELECT
            + " WHERE r.home_id=? "
            + ("" if include_retired else "AND d.state!='retired' ")
            + "ORDER BY r.id,d.id",
            (user["home_id"],),
        )
        for device in devices:
            last = self.db.one(
                "SELECT * FROM readings WHERE device_id=? ORDER BY measured_at DESC LIMIT 1",
                (device["id"],),
            )
            end = (
                (
                    datetime.fromisoformat(last["measured_at"])
                    + timedelta(seconds=last["interval_seconds"])
                )
                if last
                else None
            )
            device["last_seen"] = end.astimezone(LOCAL_TZ) if end else None
            device["online"] = bool(end and (now() - end).total_seconds() <= 300)
            device["power_w"] = (
                last["power_w"]
                if last
                and device["online"]
                and device["state"] in (DeviceState.RUNNING, DeviceState.IDLE)
                else 0
            )
        return devices

    def device(self, session, device_id, *, owner=False):
        user = self.access.require(session, owner=owner)
        result = self.db.one(
            DEVICE_SELECT + " WHERE d.id=? AND r.home_id=?", (device_id, user["home_id"])
        )
        if not result:
            raise ValidationError("Устройство не найдено в вашем доме.")
        return result

    def add_device(
        self,
        session,
        room_id,
        name,
        serial,
        kind,
        rated_w,
        critical_w,
        flexible=False,
        essential=False,
        counted=True,
    ):
        user = self.access.require(session, owner=True)
        if not self.db.one(
            "SELECT id FROM rooms WHERE id=? AND home_id=?", (room_id, user["home_id"])
        ):
            raise ValidationError("Сначала выберите комнату вашего дома.")
        rated_w = number(rated_w, "Номинальная мощность", 1)
        critical_w = number(critical_w, "Критический порог", rated_w)
        name, serial = name.strip(), serial.strip()
        if not name or not serial or max(len(name), len(serial)) > 100:
            raise ValidationError("Укажите название и серийный номер длиной до 100 символов.")
        if kind not in ("meter", "socket", "sensor"):
            raise ValidationError("Неизвестный тип устройства учёта.")
        try:
            with self.db.transaction():
                appliance_id = self.db.execute(
                    "INSERT INTO appliances(room_id,name,rated_w,flexible,essential) "
                    "VALUES(?,?,?,?,?)",
                    (room_id, name, rated_w, bool(flexible), bool(essential)),
                ).lastrowid
                device_id = self.db.execute(
                    "INSERT INTO devices(appliance_id,serial,kind,state,critical_w,counted,"
                    "created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        appliance_id,
                        serial,
                        kind,
                        DeviceState.IDLE,
                        critical_w,
                        bool(counted),
                        stamp(now()),
                    ),
                ).lastrowid
                self.db.event(
                    user["home_id"],
                    "device_added",
                    f"Зарегистрирован прибор: {name}",
                    device_id=device_id,
                )
                return device_id
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Устройство с таким серийным номером уже существует.") from exc

    def change_state(self, session, device_id, action):
        device = self.device(session, device_id, owner=True)
        if action == "schedule" and not device["flexible"]:
            raise ValidationError("Этот прибор не поддерживает перенос нагрузки.")
        result = transition(device["state"], action)
        with self.db.transaction():
            self.db.execute("UPDATE devices SET state=? WHERE id=?", (result, device_id))
            self.db.event(
                device["home_id"],
                "state_changed",
                f"{device['name']}: {result}",
                device_id=device_id,
            )
        return result

    def settings(self, session, *, owner=True):
        user = self.access.require(session, owner=owner)
        settings = self.db.one("SELECT * FROM settings WHERE home_id=?", (user["home_id"],))
        tariff = self.db.one(
            "SELECT * FROM tariffs WHERE home_id=? AND effective_at<=? "
            "ORDER BY effective_at DESC,id DESC LIMIT 1",
            (user["home_id"], stamp(now())),
        )
        return {
            **settings,
            **{k: tariff[k] for k in ("day_rate", "night_rate", "day_start", "night_start")},
        }

    def save_settings(
        self,
        session,
        *,
        monthly_budget,
        daily_kwh,
        day_rate,
        night_rate,
        day_start=7,
        night_start=23,
        notify_80=True,
        auto_protect=True,
    ):
        user = self.access.require(session, owner=True)
        monthly_budget = number(monthly_budget, "Месячный лимит расходов", 0.01)
        daily_kwh = number(daily_kwh, "Дневной лимит энергии", 0.01)
        day_rate = number(day_rate, "Дневной тариф")
        night_rate = number(night_rate, "Ночной тариф")
        if (
            type(day_start) is not int
            or type(night_start) is not int
            or not 0 <= day_start < night_start <= 23
        ):
            raise ValidationError("Начало дня должно быть раньше начала ночи (часы 0–23).")
        old = self.settings(session)
        with self.db.transaction():
            self.db.execute(
                "UPDATE settings SET monthly_budget=?,daily_kwh=?,notify_80=?,auto_protect=? "
                "WHERE home_id=?",
                (monthly_budget, daily_kwh, bool(notify_80), bool(auto_protect), user["home_id"]),
            )
            fields = ("day_rate", "night_rate", "day_start", "night_start")
            values = (day_rate, night_rate, day_start, night_start)
            if tuple(old[k] for k in fields) != values:
                self.db.execute(
                    "INSERT INTO tariffs(home_id,effective_at,day_rate,night_rate,day_start,"
                    "night_start) VALUES(?,?,?,?,?,?)",
                    (user["home_id"], stamp(now()), *values),
                )
            self.db.event(
                user["home_id"],
                "settings_changed",
                "Изменены тарифы или лимиты",
                user_id=user["id"],
            )
            self.check_limits(user["home_id"])

    def _price_segments(self, home_id, start, duration, energy):
        end = start + timedelta(seconds=duration)
        tariffs = self.db.all(
            "SELECT * FROM tariffs WHERE home_id=? AND effective_at<? ORDER BY effective_at,id",
            (home_id, stamp(end)),
        )
        segments = []
        cursor = start
        while cursor < end:
            applicable = [t for t in tariffs if t["effective_at"] <= stamp(cursor)]
            if not applicable:
                raise ValidationError("Для даты показания не задан тариф.")
            tariff = applicable[-1]
            local = cursor.astimezone(LOCAL_TZ)
            next_hour = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            future = [
                datetime.fromisoformat(t["effective_at"])
                for t in tariffs
                if t["effective_at"] > stamp(cursor)
            ]
            boundary = min([end, next_hour, *future])
            part_energy = energy * (boundary - cursor).total_seconds() / duration
            rate = (
                tariff["day_rate"]
                if tariff["day_start"] <= local.hour < tariff["night_start"]
                else tariff["night_rate"]
            )
            segments.append(
                (stamp(cursor), stamp(boundary), part_energy, part_energy * rate, tariff["id"])
            )
            cursor = boundary
        return segments

    def ingest(self, session, packet: ReadingPacket):
        device = self.device(session, packet.device_id, owner=True)
        try:
            return self._ingest(device, packet)
        except ValidationError as exc:
            with self.db.transaction():
                self.db.event(
                    device["home_id"], "invalid_reading", str(exc), device_id=device["id"]
                )
            raise

    def _ingest(self, device, packet):
        power = number(packet.power_w, "Мощность", 0, 10_000_000)
        energy = number(packet.energy_kwh, "Энергия интервала", 0, 1_000_000)
        if type(packet.interval_seconds) is not int or not 1 <= packet.interval_seconds <= 86400:
            raise ValidationError("Интервал показания должен быть от 1 секунды до суток.")
        start = packet.measured_at
        start_stamp = stamp(start)
        end = start + timedelta(seconds=packet.interval_seconds)
        if end > now() + timedelta(seconds=60):
            raise ValidationError("Интервал показания заканчивается в будущем.")
        if device["state"] == DeviceState.RETIRED:
            raise ValidationError("Нельзя принять показания списанного устройства.")
        previous = self.db.one(
            "SELECT * FROM readings WHERE device_id=? ORDER BY measured_at DESC LIMIT 1",
            (device["id"],),
        )
        if previous:
            previous_end = datetime.fromisoformat(previous["measured_at"]) + timedelta(
                seconds=previous["interval_seconds"]
            )
            if start < previous_end:
                raise ValidationError("Показание дублируется, пересекается или старше последнего.")
        segments = self._price_segments(device["home_id"], start, packet.interval_seconds, energy)
        with self.db.transaction():
            reading_id = self.db.execute(
                "INSERT INTO readings(device_id,measured_at,interval_seconds,power_w,energy_kwh,"
                "cost,tariff_id) VALUES(?,?,?,?,?,?,?)",
                (
                    device["id"],
                    start_stamp,
                    packet.interval_seconds,
                    power,
                    energy,
                    sum(s[3] for s in segments),
                    segments[0][4],
                ),
            ).lastrowid
            self.db.connection.executemany(
                "INSERT INTO reading_segments(reading_id,start_at,end_at,energy_kwh,cost) "
                "VALUES(?,?,?,?,?)",
                [(reading_id, *s[:4]) for s in segments],
            )
            self._detect_anomaly(device, packet, reading_id)
            self.check_limits(device["home_id"])
            self.db.event(
                device["home_id"],
                "reading_received",
                "Показание принято и проверено",
                device_id=device["id"],
            )
        return reading_id

    def _detect_anomaly(self, device, packet, reading_id):
        recent = self.db.all(
            "SELECT power_w FROM readings WHERE device_id=? AND id!=? AND measured_at>=? "
            "AND measured_at<? AND strftime('%H',measured_at)=strftime('%H',?) "
            "AND power_w>0 ORDER BY measured_at DESC LIMIT 24",
            (
                device["id"],
                reading_id,
                stamp(packet.measured_at - timedelta(days=7)),
                stamp(packet.measured_at),
                stamp(packet.measured_at),
            ),
        )
        baseline = sum(r["power_w"] for r in recent) / len(recent) if recent else 0
        critical = packet.power_w > device["critical_w"]
        spike = (
            len(recent) >= 5
            and packet.power_w > baseline * 2
            and packet.power_w > device["rated_w"] * 0.5
        )
        if not (critical or spike):
            return
        tariff = self.db.one(
            "SELECT day_start,night_start FROM tariffs WHERE home_id=? AND effective_at<=? "
            "ORDER BY effective_at DESC,id DESC LIMIT 1",
            (device["home_id"], stamp(packet.measured_at)),
        )
        hour = packet.measured_at.astimezone(LOCAL_TZ).hour
        night = hour < tariff["day_start"] or hour >= tariff["night_start"]
        kind = "critical" if critical else ("night_spike" if night else "spike")
        reference = device["critical_w"] if critical else baseline
        deviation = (packet.power_w / reference - 1) * 100
        message = (
            f"{device['name']}: {packet.power_w:.0f} Вт, "
            f"{'превышен критический порог' if critical else 'скачок потребления'} "
            f"на {deviation:.0f}%."
        )
        settings = self.db.one("SELECT * FROM settings WHERE home_id=?", (device["home_id"],))
        if (
            critical
            and settings["auto_protect"]
            and device["state"] in (DeviceState.IDLE, DeviceState.RUNNING, DeviceState.FLEXIBLE)
        ):
            state = transition(device["state"], "anomaly")
            self.db.execute("UPDATE devices SET state=? WHERE id=?", (state, device["id"]))
            message += " Прибор аварийно обесточен в системе."
            self.db.event(device["home_id"], "emergency_shutdown", message, device_id=device["id"])
        self.db.execute(
            "INSERT INTO anomalies(device_id,reading_id,kind,deviation,message,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (device["id"], reading_id, kind, deviation, message, stamp(packet.measured_at)),
        )
        self.notifications.send(device["home_id"], kind, message, f"anomaly:{reading_id}")

    def check_limits(self, home_id):
        from .reports import totals

        settings = self.db.one("SELECT * FROM settings WHERE home_id=?", (home_id,))
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        month = today.replace(day=1)
        _, cost = totals(self.db, home_id, month, now())
        energy, _ = totals(self.db, home_id, today, now())
        budget = settings["monthly_budget"]
        if cost >= budget:
            self.notifications.send(
                home_id,
                "budget",
                f"Месячный лимит расходов превышен: {cost:.2f} / {budget:.2f} BYN.",
                f"budget:100:{month:%Y-%m}",
            )
        elif settings["notify_80"] and cost >= budget * 0.8:
            self.notifications.send(
                home_id,
                "budget",
                f"Достигнуто 80% месячного лимита: {cost:.2f} / {budget:.2f} BYN.",
                f"budget:80:{month:%Y-%m}",
            )
        if energy >= settings["daily_kwh"]:
            self.notifications.send(
                home_id,
                "daily",
                f"Превышен дневной лимит: {energy:.2f} / {settings['daily_kwh']:.2f} кВт·ч.",
                f"daily:{today:%Y-%m-%d}",
            )

    def events(self, session):
        user = self.access.require(session, owner=True)
        return self.db.all(
            "SELECT * FROM events WHERE home_id=? ORDER BY id DESC LIMIT 300",
            (user["home_id"],),
        )
