import csv
from datetime import datetime

import pytest

from energy_home.automation import AutomationService
from energy_home.domain import (
    LOCAL_TZ,
    AccessDenied,
    DeviceState,
    Navigation,
    ReadingPacket,
    Role,
    Screen,
    ValidationError,
    stamp,
    transition,
)
from energy_home.extensions import BuildingService, HomeAssistantAdapter, VoiceAssistant
from energy_home.gateways import SensorGateway, import_csv


def packet(device, day=10, hour=12, power=1000, energy=1.0, minutes=0, duration=3600):
    return ReadingPacket(
        device, datetime(2026, 11, day, hour, minutes, tzinfo=LOCAL_TZ), power, energy, duration
    )


def test_registration_hash_and_atomic_duplicate(system):
    db, access, *_ = system
    user = db.one("SELECT password FROM users WHERE login='owner'")
    assert user["password"].startswith("pbkdf2_sha256$")
    assert "testpassword" not in user["password"]
    with pytest.raises(ValidationError):
        access.register_home("Другой", "OWNER", "testpassword", "Другой дом")
    assert db.one("SELECT COUNT(*) AS n FROM homes")["n"] == 1
    with pytest.raises(AccessDenied):
        access.login("owner", "wrongpassword")


def test_family_permissions_and_reading_visibility(system):
    _, access, energy, reports, owner, family, room, device = system
    energy.ingest(owner, packet(device))
    assert energy.devices(family)[0]["id"] == device
    assert reports.build(family).energy_kwh == 1
    for action in (
        lambda: energy.add_room(family, "Гараж"),
        lambda: energy.add_device(family, room, "TV", "TV", "socket", 100, 500),
        lambda: energy.change_state(family, device, "start"),
        lambda: energy.settings(family),
        lambda: energy.save_settings(
            family, monthly_budget=1, daily_kwh=1, day_rate=1, night_rate=1
        ),
        lambda: access.add_family(family, "Новый", "new", "password"),
        lambda: energy.ingest(family, packet(device)),
        lambda: energy.events(family),
    ):
        with pytest.raises(AccessDenied):
            action()


def test_other_home_isolation_and_revoked_session(system):
    db, access, energy, reports, owner, family, _, device = system
    other = access.register_home("Другой", "other", "testpassword", "Другой дом")
    assert energy.devices(other) == []
    assert reports.build(other).energy_kwh == 0
    with pytest.raises(ValidationError):
        energy.ingest(other, packet(device))
    family_id = db.one("SELECT id FROM users WHERE login='family'")["id"]
    access.set_family_active(owner, family_id, False)
    with pytest.raises(AccessDenied):
        reports.build(family)
    access.logout(owner)
    with pytest.raises(AccessDenied):
        energy.rooms(owner)


def test_day_night_split_and_filtered_report(system):
    _, _, energy, reports, owner, _, room, device = system
    energy.ingest(owner, packet(device, hour=22, minutes=30, energy=2))
    report = reports.build(owner, "day", datetime(2026, 11, 10, tzinfo=LOCAL_TZ), room, device)
    assert report.energy_kwh == pytest.approx(2)
    assert report.cost == pytest.approx(0.42)
    assert dict(report.series)["22:00"] == 1
    assert dict(report.series)["23:00"] == 1
    assert sum(v for _, v in report.series) == 2


def test_tariff_change_does_not_reprice_history_and_splits_effective_time(system, clock):
    db, _, energy, reports, owner, _, _, device = system
    energy.ingest(owner, packet(device, hour=12, energy=2))
    energy.save_settings(owner, monthly_budget=100, daily_kwh=25, day_rate=1, night_rate=0.5)
    clock[0] = datetime(2026, 11, 11, 16, tzinfo=LOCAL_TZ)
    energy.ingest(owner, packet(device, day=11, hour=14, energy=2))
    report = reports.build(owner)
    assert report.cost == pytest.approx(0.56 + 1.28)
    assert db.one("SELECT COUNT(*) AS n FROM tariffs")["n"] == 2


@pytest.mark.parametrize(
    "power,energy,duration",
    [
        (-1, 1, 60),
        (float("nan"), 1, 60),
        (float("inf"), 1, 60),
        (1, -1, 60),
        (1, float("nan"), 60),
        (1, 1, 0),
        (1, 1, 86401),
    ],
)
def test_invalid_packet_is_logged_without_saving(system, power, energy, duration):
    db, _, energy_service, _, owner, _, _, device = system
    with pytest.raises(ValidationError):
        energy_service.ingest(owner, packet(device, power=power, energy=energy, duration=duration))
    assert db.one("SELECT COUNT(*) AS n FROM readings")["n"] == 0
    assert db.one("SELECT kind FROM events ORDER BY id DESC LIMIT 1")["kind"] == "invalid_reading"


def test_duplicate_overlap_naive_and_future_are_rejected(system):
    db, _, energy, _, owner, _, _, device = system
    energy.ingest(owner, packet(device))
    for bad in [
        packet(device),
        packet(device, minutes=30),
        packet(device, hour=11),
        ReadingPacket(device, datetime(2026, 11, 11), 10, 1),
        packet(device, day=12),
    ]:
        with pytest.raises(ValidationError):
            energy.ingest(owner, bad)
    assert db.one("SELECT COUNT(*) AS n FROM readings")["n"] == 1


def test_critical_anomaly_shutdown_reset_and_history(system):
    db, _, energy, reports, owner, family, _, device = system
    energy.change_state(owner, device, "start")
    energy.ingest(owner, packet(device, power=6000, energy=6))
    assert energy.device(owner, device)["state"] == DeviceState.EMERGENCY
    assert energy.devices(owner)[0]["power_w"] == 0
    assert len(energy.notifications.list(family)) == 1
    assert reports.build(owner).anomalies[0]["kind"] == "critical"
    with pytest.raises(ValidationError):
        energy.change_state(owner, device, "enable")
    energy.change_state(owner, device, "reset")
    assert energy.device(owner, device)["state"] == DeviceState.OFF
    energy.change_state(owner, device, "enable")
    assert energy.device(owner, device)["state"] == DeviceState.IDLE
    assert db.one("SELECT COUNT(*) AS n FROM anomalies")["n"] == 1


def test_budget_notifications_are_deduplicated_and_read_per_user(system):
    _, _, energy, _, owner, family, _, device = system
    energy.save_settings(owner, monthly_budget=0.30, daily_kwh=0.5, day_rate=0.28, night_rate=0.14)
    energy.ingest(owner, packet(device, day=11, hour=10, energy=1))
    assert {n["kind"] for n in energy.notifications.list(owner)} == {"budget", "daily"}
    energy.ingest(owner, packet(device, day=11, hour=11, energy=0.01))
    assert len(energy.notifications.list(owner)) == 2
    energy.notifications.mark_read(owner)
    assert all(n["is_read"] for n in energy.notifications.list(owner))
    assert all(not n["is_read"] for n in energy.notifications.list(family))
    energy.ingest(owner, packet(device, day=11, hour=12, energy=1))
    assert len(energy.notifications.list(owner)) == 3


def test_current_comparison_has_equal_elapsed_time(system):
    _, _, energy, reports, owner, _, _, device = system
    for month, day, energy_kwh in [(10, 1, 2), (10, 20, 50), (11, 1, 4)]:
        energy.ingest(
            owner,
            ReadingPacket(
                device, datetime(2026, month, day, 12, tzinfo=LOCAL_TZ), 1000, energy_kwh, 3600
            ),
        )
    report = reports.build(owner)
    assert report.previous_kwh == 2
    assert report.energy_kwh == 4
    assert report.change_percent == 100


def test_total_meter_can_be_excluded_and_retired_history_remains(system):
    _, _, energy, reports, owner, _, room, device = system
    meter = energy.add_device(
        owner, room, "Общий счётчик", "MAIN", "meter", 10000, 20000, counted=False
    )
    energy.ingest(owner, packet(device, energy=1))
    energy.ingest(owner, packet(meter, energy=1))
    assert reports.build(owner).energy_kwh == 1
    energy.change_state(owner, device, "retire")
    assert reports.build(owner).energy_kwh == 1
    with pytest.raises(ValidationError):
        energy.ingest(owner, packet(device, hour=13))


def test_csv_partial_import_and_export(system, tmp_path):
    db, _, energy, reports, owner, _, _, _ = system
    source = tmp_path / "readings.csv"
    source.write_text(
        "serial,measured_at,power_w,energy_kwh,interval_seconds\n"
        "KETTLE-1,2026-11-10T12:00:00+03:00,1000,1,3600\n"
        "KETTLE-1,2026-11-10T13:00:00+03:00,-1,1,3600\n"
        "UNKNOWN,2026-11-10T14:00:00+03:00,1000,1,3600\n",
        encoding="utf-8",
    )
    count, errors = import_csv(energy, owner, source)
    assert count == 1 and len(errors) == 2
    assert db.one("SELECT COUNT(*) AS n FROM readings")["n"] == 1
    target = tmp_path / "report.csv"
    reports.export_csv(owner, reports.build(owner), target)
    with target.open(encoding="utf-8-sig") as file:
        rows = list(csv.reader(file, delimiter=";"))
    assert rows[0][0] == "Тип"
    assert float(rows[1][3]) == 1
    assert reports._safe_cell("=1+1") == "'=1+1"


def test_gateway_processes_packets_and_returns_errors(system):
    _, _, energy, _, owner, _, _, device = system
    gateway = SensorGateway(energy)
    gateway.submit(owner, packet(device))
    gateway.submit(owner, packet(device))
    count, errors = gateway.drain()
    assert count == 2 and len(errors) == 1


def test_device_state_and_screen_machines():
    assert transition("off", "enable") == "idle"
    assert transition("running", "schedule") == "flexible"
    assert transition("flexible", "night") == "running"
    with pytest.raises(ValidationError):
        transition("emergency", "start")
    navigation = Navigation()
    navigation.go(Screen.HOME, Role.FAMILY)
    with pytest.raises(AccessDenied):
        navigation.go(Screen.SETTINGS, Role.FAMILY)
    navigation.go(Screen.DEVICES, Role.FAMILY)
    with pytest.raises(ValidationError):
        navigation.go(Screen.REPORTS, Role.FAMILY)
    navigation.go(Screen.HOME, Role.OWNER)
    navigation.go(Screen.SETTINGS, Role.OWNER)


def test_automation_protects_essential_appliances_and_starts_at_night(system, clock):
    _, _, energy, _, owner, family, room, device = system
    fridge = energy.add_device(
        owner, room, "Холодильник", "FRIDGE", "socket", 100, 400, essential=True
    )
    automation = AutomationService(energy)
    assert automation.apply(owner, "night_tariff") == 1
    assert energy.device(owner, device)["state"] == "flexible"
    assert energy.device(owner, fridge)["state"] == "idle"
    clock[0] = datetime(2026, 11, 11, 23, tzinfo=LOCAL_TZ)
    assert automation.tick(owner) == 1
    assert energy.device(owner, device)["state"] == "running"
    automation.apply(owner, "vacation")
    assert energy.device(owner, fridge)["state"] == "idle"
    with pytest.raises(AccessDenied):
        automation.apply(family, "vacation")


def test_common_area_allocation_sums_and_is_isolated(system):
    db, access, _, _, owner, family, *_ = system
    building = BuildingService(db, access)
    building.add_apartment(owner, "1", 50, 2, 100)
    building.add_apartment(owner, "2", 100, 1, 150)
    rows = building.distribute(family, 90, "area")
    assert [r["common_kwh"] for r in rows] == [30, 60]
    assert sum(r["common_kwh"] for r in rows) == 90
    assert building.distribute(owner, 90, "residents")[0]["common_kwh"] == 60
    assert building.distribute(owner, 90, "equal")[0]["common_kwh"] == 45
    with pytest.raises(AccessDenied):
        building.add_apartment(family, "3", 50, 1, 100)
    other = access.register_home("Другой", "other", "testpassword", "Другой")
    assert building.apartments(other) == []


def test_assistant_permissions_and_home_assistant_adapter(system, monkeypatch):
    _, _, energy, reports, _, family, *_ = system
    assistant = VoiceAssistant(energy, reports, AutomationService(energy))
    assert "Сегодня" in assistant.execute(family, assistant.recognize("текущее потребление"))
    with pytest.raises(AccessDenied):
        assistant.execute(family, assistant.recognize("отпуск"))
    adapter = HomeAssistantAdapter("http://localhost:8123", "token")
    monkeypatch.setattr(
        adapter,
        "_request",
        lambda path: {"state": "1.5", "attributes": {"unit_of_measurement": "kW"}},
    )
    assert adapter.power_w("sensor.power") == 1500
    with pytest.raises(ValidationError):
        adapter.power_w("../../bad")


def test_reading_cost_and_users_persist_on_reopen(tmp_path):
    from energy_home.auth import AccessControl
    from energy_home.reports import ReportService
    from energy_home.services import EnergyService
    from energy_home.storage import Database

    path = tmp_path / "energy.sqlite3"
    db = Database(path)
    access = AccessControl(db)
    energy = EnergyService(db, access)
    owner = access.register_home("Имя", "persist", "testpassword", "Дом")
    room = energy.add_room(owner, "Комната")
    device = energy.add_device(owner, room, "Прибор", "SAVE", "socket", 1000, 3000)
    energy.ingest(owner, packet(device))
    db.close()
    reopened = Database(path)
    access = AccessControl(reopened)
    session = access.login("persist", "testpassword")
    assert ReportService(reopened, access).build(session).energy_kwh == 1
    assert reopened.one("PRAGMA integrity_check")["integrity_check"] == "ok"
    reopened.close()


def test_interval_crossing_midnight_is_split_between_days(system):
    _, _, energy, reports, owner, *_rest, device = system
    energy.ingest(owner, packet(device, hour=23, minutes=30, energy=2))
    today = reports.dashboard(owner)
    yesterday = reports.build(owner, "day", datetime(2026, 11, 10, tzinfo=LOCAL_TZ))
    assert today["daily_kwh"] == pytest.approx(1)
    assert yesterday.energy_kwh == pytest.approx(1)
    assert stamp(datetime(2026, 11, 11, tzinfo=LOCAL_TZ)).startswith("2026-11-10T21")


def test_night_spike_uses_comparable_hours_and_not_only_midnight(system):
    _, _, energy, reports, owner, _, _, device = system
    for day in range(4, 10):
        energy.ingest(owner, packet(device, day=day, hour=23, power=100, energy=0.1))
    energy.ingest(owner, packet(device, day=10, hour=23, power=1200, energy=1.2))
    assert reports.build(owner).anomalies[0]["kind"] == "night_spike"
    assert energy.device(owner, device)["state"] == "idle"


def test_complete_month_compares_all_days_of_longer_previous_month(system):
    _, _, energy, reports, owner, _, _, device = system
    for month, day, amount in [(8, 31, 7), (9, 30, 8)]:
        energy.ingest(
            owner,
            ReadingPacket(
                device, datetime(2026, month, day, 12, tzinfo=LOCAL_TZ), 1000, amount, 3600
            ),
        )
    report = reports.build(owner, "month", datetime(2026, 9, 1, tzinfo=LOCAL_TZ))
    assert report.previous_kwh == 7
    assert report.energy_kwh == 8
