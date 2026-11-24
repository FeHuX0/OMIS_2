"""Seed initial demo state 1:1 with Mockups 5.3 (pages 18–20) of 1laba_full_fc613401.pdf."""

from __future__ import annotations

from datetime import datetime

from energy_home.domain import (
    LOCAL_TZ,
    Anomaly,
    Appliance,
    Event,
    Home,
    Limit,
    MeteringDevice,
    Notification,
    Role,
    Room,
    Tariff,
    User,
    hash_password,
)
from energy_home.subsystems import (
    AccessControlSubsystem,
    AnalysisSubsystem,
    ApplicationInterface,
    DataCollectionSubsystem,
    NotificationSubsystem,
    ReportingSubsystem,
    StorageSubsystem,
)

DEMO_OWNER_LOGIN = "user@example.com"
DEMO_FAMILY_LOGIN = "family@example.com"
DEMO_PASSWORD = "password123"


def create_system() -> ApplicationInterface:
    """Assemble all 7 subsystems from Diagram 4.1 and seed mockup data from Section 5.3."""
    storage = StorageSubsystem()
    access_control = AccessControlSubsystem(storage)
    collection = DataCollectionSubsystem(storage)
    analysis = AnalysisSubsystem(storage)
    notifications = NotificationSubsystem(storage, analysis)
    reporting = ReportingSubsystem(storage, analysis)
    app_interface = ApplicationInterface(
        storage=storage,
        collection=collection,
        analysis=analysis,
        notifications=notifications,
        reporting=reporting,
        access_control=access_control,
    )

    # Tariff and limits matching Mockup 5 "Settings" (page 20)
    tariff = Tariff(day_rate=5.50, night_rate=2.10, day_start=7, night_start=23)
    limit = Limit(
        monthly_budget_byn=5000.0,
        daily_limit_kwh=80.0,
        notify_at_80=True,
        period="month",
    )
    storage.save(tariff)
    storage.save(limit)

    # Rooms and appliances matching Mockup 3 "Devices" (page 19)
    kitchen = Room(name="Кухня", id=storage.next_room_id(), collapsed_by_default=False)

    smart_meter = Appliance(
        name="Умный счётчик",
        power=0.0,
        metering_device=MeteringDevice(
            device_type="Счётчик",
            serial_number="MTR-001",
            tariff=tariff,
            current_power_w=0.0,
            active=True,
        ),
        critical_power_w=10000.0,
        essential=True,
        display_status="активно",
        id=storage.next_appliance_id(),
    )
    smart_meter.state_machine.turn_on()
    smart_meter.state_machine.start_operation()

    fridge = Appliance(
        name="Холодильник",
        power=150.0,
        metering_device=MeteringDevice(
            device_type="Умная розетка",
            serial_number="SCK-KITCHEN-01",
            tariff=tariff,
            current_power_w=150.0,
            active=True,
        ),
        critical_power_w=900.0,
        essential=True,
        display_status="потребление: 150 Вт",
        id=storage.next_appliance_id(),
    )
    fridge.state_machine.turn_on()
    fridge.state_machine.start_operation()
    kitchen.add_appliance(smart_meter)
    kitchen.add_appliance(fridge)

    bedroom = Room(name="Спальня", id=storage.next_room_id(), collapsed_by_default=False)
    socket_1 = Appliance(
        name="Розетка 1",
        power=600.0,
        metering_device=MeteringDevice(
            device_type="Умная розетка",
            serial_number="SCK-BED-01",
            tariff=tariff,
            current_power_w=0.0,
            active=False,
        ),
        critical_power_w=2500.0,
        display_status="не активно",
        id=storage.next_appliance_id(),
    )
    bedroom.add_appliance(socket_1)

    hall = Room(name="Зал", id=storage.next_room_id(), collapsed_by_default=False)
    air_conditioner = Appliance(
        name="Кондиционер",
        power=1250.0,
        metering_device=MeteringDevice(
            device_type="Датчик",
            serial_number="SNS-HALL-01",
            tariff=tariff,
            current_power_w=1250.0,
            active=True,
        ),
        critical_power_w=3500.0,
        flexible_load=True,
        display_status="авторежим",
        id=storage.next_appliance_id(),
    )
    air_conditioner.state_machine.turn_on()
    air_conditioner.state_machine.start_operation()
    air_conditioner.state_machine.switch_to_off_peak()
    hall.add_appliance(air_conditioner)

    bathroom = Room(name="Санузел", id=storage.next_room_id(), collapsed_by_default=True)
    washer = Appliance(
        name="Стиральная машина",
        power=2000.0,
        metering_device=MeteringDevice(
            device_type="Умная розетка",
            serial_number="SCK-BATH-01",
            tariff=tariff,
            current_power_w=0.0,
            active=False,
        ),
        critical_power_w=4000.0,
        flexible_load=True,
        display_status="не активно",
        id=storage.next_appliance_id(),
    )
    bathroom.add_appliance(washer)

    # Home base load yields 3.4 kW current power and 102.5 kWh today (Mockup 2, page 18)
    home = Home(
        address="г. Минск, ул. Энергетиков, д. 12, кв. 45",
        rooms=[kitchen, bedroom, hall, bathroom],
        home_type="apartment",
        base_daily_consumption_kwh=102.5,
        base_power_kw=2.0,  # + 0.15 kW (Fridge) + 1.25 kW (AC) = 3.4 kW
    )
    storage.save(home)

    owner = User(
        login=DEMO_OWNER_LOGIN,
        password=hash_password(DEMO_PASSWORD),
        role=Role.OWNER,
        home=home,
        name="Владелец дома",
    )
    family_member = User(
        login=DEMO_FAMILY_LOGIN,
        password=hash_password(DEMO_PASSWORD),
        role=Role.FAMILY_MEMBER,
        home=home,
        name="Член семьи",
    )
    storage.save(owner)
    storage.save(family_member)

    # Chart series for Reports window (Mockup 4, page 19)
    storage._period_series = {
        "week": {
            "title": "График расхода по дням (кВт·ч)",
            "subtitle": "Сегодня: 22.03.2027 - Пятница",
            "labels": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
            "values": [16.5, 13.3, 23.3, 15.1, 7.8, 0.0, 0.0],
            "y_step": 5.0,
            "y_max": 25.0,
        },
        "day": {
            "title": "График расхода по часам (кВт·ч)",
            "subtitle": "Сегодня: 22.03.2027 - Пятница",
            "labels": ["00", "04", "08", "12", "16", "20", "23"],
            "values": [1.2, 0.9, 2.4, 3.1, 2.8, 4.2, 1.9],
            "y_step": 1.0,
            "y_max": 5.0,
        },
        "month": {
            "title": "График расхода по неделям месяца (кВт·ч)",
            "subtitle": "Март 2027 · Сегодня: 22.03.2027 - Пятница",
            "labels": ["Нед 1", "Нед 2", "Нед 3", "Нед 4"],
            "values": [94.2, 108.6, 112.4, 76.0],
            "y_step": 25.0,
            "y_max": 125.0,
        },
    }

    storage.save(
        Anomaly(
            anomaly_type="Скачок мощности",
            magnitude=23.0,
            timestamp=datetime(2027, 3, 20, 2, 34, tzinfo=LOCAL_TZ),
            location="линии кухни",
            custom_time_label="20.03.2027 (среда) 02:34",
        )
    )
    storage.save(
        Notification(
            message="Внимание! Превышен лимит дневного потребления энергии.",
            notification_type="limit",
            sent_at=datetime(2027, 3, 22, 12, 0, tzinfo=LOCAL_TZ),
        )
    )
    storage.save(
        Event(
            event_type="system_initialized",
            timestamp=datetime.now(LOCAL_TZ),
            description="Загружены начальные данные 1:1 по отчёту 1laba_full.pdf",
        )
    )
    return app_interface
