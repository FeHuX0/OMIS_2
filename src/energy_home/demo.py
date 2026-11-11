import math
import random
from datetime import timedelta

from .domain import now, stamp

DEMO_PASSWORD = "Energy2026!"


def seed_demo(energy):
    """Идемпотентная демонстрация, отдельная от зарегистрированных пользователем домов."""
    existing = energy.db.one("SELECT id FROM users WHERE login='owner@demo.local'")
    if existing:
        return
    access, db = energy.access, energy.db
    session = access.register_home(
        "Алексей", "owner@demo.local", DEMO_PASSWORD, "Мой дом · демонстрация", "Минск"
    )
    access.add_family(session, "Член семьи", "family@demo.local", DEMO_PASSWORD)
    kitchen = energy.add_room(session, "Кухня")
    living = energy.add_room(session, "Гостиная")
    bathroom = energy.add_room(session, "Ванная")
    bedroom = energy.add_room(session, "Спальня")
    definitions = [
        (kitchen, "Холодильник", "DEMO-FRIDGE", "socket", 180, 900, False, True),
        (kitchen, "Посудомоечная машина", "DEMO-DISH", "socket", 1600, 3500, True, False),
        (living, "Кондиционер", "DEMO-AC", "sensor", 1200, 3500, False, False),
        (living, "Телевизор", "DEMO-TV", "socket", 160, 600, False, False),
        (bathroom, "Стиральная машина", "DEMO-WASH", "socket", 2000, 4000, True, False),
        (bedroom, "Освещение", "DEMO-LIGHT", "sensor", 120, 500, False, False),
    ]
    devices = []
    for definition in definitions:
        device_id = energy.add_device(session, *definition)
        energy.change_state(session, device_id, "start")
        devices.append(energy.device(session, device_id))
    user = access.require(session)
    rng = random.Random(42)
    end = now().replace(minute=0, second=0, microsecond=0)
    start = end.replace(hour=0) - timedelta(days=65)
    tariff = db.one("SELECT id FROM tariffs WHERE home_id=?", (user["home_id"],))["id"]
    # Историческая демозагрузка пакетная: она не имитирует приём из реального оборудования.
    with db.transaction():
        cursor = start
        while cursor < end:
            for d in devices:
                active = 7 <= cursor.hour < 23
                factor = 0.25 if d["essential"] else (0.25 if active else 0.015)
                if d["flexible"]:
                    factor = 0.65 if cursor.hour in (19, 20) else 0.005
                trend = 1 + 0.09 * math.sin((cursor - start).days / 8)
                power = d["rated_w"] * factor * rng.uniform(0.8, 1.2) * trend
                energy_kwh = power / 1000
                cost = energy_kwh * (0.28 if active else 0.14)
                reading_id = db.execute(
                    "INSERT INTO readings(device_id,measured_at,interval_seconds,power_w,"
                    "energy_kwh,cost,tariff_id) VALUES(?,?,?,?,?,?,?)",
                    (d["id"], stamp(cursor), 3600, power, energy_kwh, cost, tariff),
                ).lastrowid
                db.execute(
                    "INSERT INTO reading_segments(reading_id,start_at,end_at,energy_kwh,cost) "
                    "VALUES(?,?,?,?,?)",
                    (
                        reading_id,
                        stamp(cursor),
                        stamp(cursor + timedelta(hours=1)),
                        energy_kwh,
                        cost,
                    ),
                )
            cursor += timedelta(hours=1)
        db.event(user["home_id"], "demo_seeded", "Загружены 65 дней демонстрационных данных")
        energy.check_limits(user["home_id"])
    access.logout(session)
