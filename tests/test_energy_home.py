"""Unit and integration tests for Energy Home (Diagrams 4.1, 4.2.1–4.2.3, 5.1, 5.2, and HTTP API)."""

from __future__ import annotations

import unittest
from datetime import datetime

from energy_home.domain import (
    LOCAL_TZ,
    AccessDeniedError,
    Anomaly,
    Appliance,
    Event,
    Home,
    MeteringDevice,
    Reading,
    Role,
    Room,
    Tariff,
    User,
)
from energy_home.extensions import (
    ApartmentBuilding,
    CommonZone,
    DeviceControlModule,
    DistributionRule,
    EnergyEfficiencyRating,
    GoogleHomeAdapter,
    HomeAssistantAdapter,
    LoadScheduler,
    Rule,
    RuleSubsystem,
    Scenario,
    VoiceAssistant,
    YandexAliceAdapter,
)
from energy_home.seed import DEMO_FAMILY_LOGIN, DEMO_OWNER_LOGIN, DEMO_PASSWORD, create_system
from energy_home.state_machine import (
    DeviceState,
    DeviceStateMachine,
    WindowState,
    WindowTransitionFSM,
)
from server import EnergyHomeApp


class TestBaseClassDiagram41(unittest.TestCase):
    """Verify 1:1 implementation of Base Class Diagram 4.1 (page 13)."""

    def test_domain_classes_and_methods(self) -> None:
        tariff = Tariff(day_rate=5.50, night_rate=2.10)
        device = MeteringDevice(device_type="Счётчик", serial_number="SN-100", tariff=tariff, current_power_w=1000.0)
        reading = device.read_measurement()
        self.assertIsInstance(reading, Reading)
        self.assertGreater(reading.value, 0)

        appliance = Appliance(name="Холодильник", power=150.0, metering_device=device)
        room = Room(name="Кухня", appliances=[appliance])
        home = Home(address="Минск", rooms=[room])
        self.assertAlmostEqual(home.get_total_consumption(), reading.value, places=2)

        user = User(login="owner@test.by", password="secret", role=Role.OWNER, home=home)
        self.assertTrue(user.authenticate("secret"))
        self.assertFalse(user.authenticate("wrong"))
        self.assertEqual(len(user.events), 1)
        self.assertIsInstance(user.events[0], Event)

    def test_subsystems_zero_arg_and_full_calls(self) -> None:
        app_interface = create_system()
        # Zero-argument calls matching Diagram 4.1 signatures
        collected = app_interface.collection.poll_devices()
        self.assertGreaterEqual(len(collected), 1)
        app_interface.collection.save_readings()

        consumption = app_interface.analysis.calculate_consumption()
        self.assertGreater(consumption, 0)
        anomalies = app_interface.analysis.detect_anomalies()
        self.assertGreaterEqual(len(anomalies), 1)

        app_interface.notifications.send_notification()
        self.assertGreaterEqual(len(app_interface.storage.find("notifications")), 2)

        report = app_interface.reporting.generate_report()
        chart = app_interface.reporting.build_chart()
        self.assertEqual(report.chart.labels, ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"])
        self.assertEqual(chart.values, [16.5, 13.3, 23.3, 15.1, 7.8, 0.0, 0.0])

        metrics = app_interface.show_consumption()
        self.assertIn("current_power_kw", metrics)
        settings = app_interface.configure_limit(monthly_budget_byn=6000.0, day_rate=5.80)
        self.assertEqual(settings["monthly_budget_byn"], 6000.0)
        self.assertEqual(settings["day_rate"], 5.80)

    def test_rbac_owner_vs_family(self) -> None:
        app_interface = create_system()
        _, owner = app_interface.access_control.login(DEMO_OWNER_LOGIN, DEMO_PASSWORD)
        _, family = app_interface.access_control.login(DEMO_FAMILY_LOGIN, DEMO_PASSWORD)

        self.assertTrue(app_interface.access_control.check_permissions(owner, "configure_tariffs_and_limits"))
        self.assertFalse(app_interface.access_control.check_permissions(family, "configure_tariffs_and_limits"))
        self.assertTrue(app_interface.access_control.check_permissions(family, "view_reports"))

        with self.assertRaises(AccessDeniedError):
            app_interface.configure_limit(user=family, monthly_budget_byn=1000.0)


class TestStateMachines51And52(unittest.TestCase):
    """Verify State Diagrams 5.1 and 5.2 (pages 16–17)."""

    def test_device_lifecycle_51(self) -> None:
        fsm = DeviceStateMachine()
        self.assertEqual(fsm.outer_state, DeviceState.OFF)
        self.assertEqual(fsm.inner_state, DeviceState.POWER_OFF_MODE)

        fsm.turn_on()
        self.assertEqual(fsm.current_state, DeviceState.IDLE_WAITING)

        fsm.start_operation()
        self.assertEqual(fsm.current_state, DeviceState.NORMAL_OPERATION)

        fsm.switch_to_off_peak()
        self.assertEqual(fsm.current_state, DeviceState.FLEXIBLE_LOAD)

        fsm.finish_flexible_cycle()
        self.assertEqual(fsm.current_state, DeviceState.NORMAL_OPERATION)

        notified = False

        def on_notify() -> None:
            nonlocal notified
            notified = True

        fsm.trigger_anomaly(critical_threshold_exceeded=True, notify_callback=on_notify)
        self.assertTrue(notified)
        self.assertEqual(fsm.outer_state, DeviceState.EMERGENCY_MODE)
        self.assertEqual(fsm.inner_state, DeviceState.LINE_DISCONNECTED)

        with self.assertRaises(ValueError):
            fsm.turn_on()

        fsm.clear_fault()
        self.assertEqual(fsm.outer_state, DeviceState.OFF)

    def test_window_transitions_52(self) -> None:
        nav = WindowTransitionFSM()
        self.assertEqual(nav.current_window, WindowState.AUTH_WINDOW)

        nav.transition(WindowState.MAIN_WINDOW)
        with self.assertRaises(PermissionError):
            nav.transition(WindowState.SETTINGS_WINDOW, is_owner=False)

        nav.transition(WindowState.DEVICES_WINDOW)
        with self.assertRaises(ValueError):
            nav.transition(WindowState.REPORTS_WINDOW)

        nav.transition(WindowState.MAIN_WINDOW)
        nav.transition(WindowState.SETTINGS_WINDOW, is_owner=True)
        nav.transition(WindowState.MAIN_WINDOW)
        nav.transition(WindowState.AUTH_WINDOW)


class TestExtensions42(unittest.TestCase):
    """Verify the 3 extension diagrams 4.2.1, 4.2.2, 4.2.3 (pages 14–15)."""

    def test_voice_and_smart_home_421(self) -> None:
        for adapter in (YandexAliceAdapter(), GoogleHomeAdapter(), HomeAssistantAdapter()):
            adapter.send_command("Кондиционер", "off")
            ev = adapter.get_events()
            self.assertIsInstance(ev, Event)
        assistant = VoiceAssistant()
        cmd = assistant.recognize_command("Текущее потребление")
        self.assertEqual(cmd.intent, "consumption")
        assistant.speak_response("3.4 кВт")
        self.assertEqual(assistant.last_response, "3.4 кВт")

    def test_apartment_building_422(self) -> None:
        building = ApartmentBuilding(
            address="пр. Независимости, 10",
            common_zones=[CommonZone("Подъезд", 35.0), CommonZone("Лифт", 45.0), CommonZone("Парковка", 20.0)],
            apartments=[Home("кв. 1", base_daily_consumption_kwh=100.0)],
            distribution_rule=DistributionRule("by_area"),
        )
        self.assertEqual(building.get_total_consumption(), 200.0)
        shares = building.distribution_rule.distribute(100.0, {"кв. 1": 60.0, "кв. 2": 40.0})
        self.assertEqual(shares["кв. 1"], 60.0)
        self.assertEqual(shares["кв. 2"], 40.0)
        rating = EnergyEfficiencyRating(apartment_consumption=80.0, building_average=100.0)
        self.assertEqual(rating.compare_with_average(), 20.0)
        self.assertEqual(rating.score, 70.0)

    def test_smart_control_423(self) -> None:
        appliance = Appliance(name="Стиральная машина", power=2000.0, flexible_load=True)
        control = DeviceControlModule()
        control.turn_on(appliance)
        self.assertEqual(appliance.state_machine.current_state, DeviceState.NORMAL_OPERATION)

        scheduler = LoadScheduler(control)
        scheduler.reschedule_load(appliance, "23:00")
        self.assertEqual(appliance.state_machine.current_state, DeviceState.FLEXIBLE_LOAD)

        scenario = Scenario(name="Отпуск", appliances=[appliance], control_module=control)
        scenario.execute()
        self.assertEqual(appliance.state_machine.outer_state, DeviceState.OFF)

        rule_sub = RuleSubsystem(rules=[Rule(condition="скачок", action="отключить")])
        anomaly = Anomaly(anomaly_type="Скачок мощности", magnitude=23.0, timestamp=datetime(2027, 3, 20, 2, 34, tzinfo=LOCAL_TZ))
        self.assertTrue(rule_sub.check_rule(anomaly))


class TestHttpApiFlow(unittest.TestCase):
    """Integration test for EnergyHomeApp HTTP API controller."""

    def test_full_api_journey(self) -> None:
        app = EnergyHomeApp()

        status, err = app.handle_api("POST", "/api/login", {}, {"login": DEMO_OWNER_LOGIN, "password": "bad"}, None)
        self.assertEqual(status, 403)
        self.assertIn("error", err)

        status, res = app.handle_api(
            "POST", "/api/login", {}, {"login": DEMO_OWNER_LOGIN, "password": DEMO_PASSWORD}, None
        )
        self.assertEqual(status, 200)
        token = res["token"]
        self.assertEqual(res["state"]["main"]["current_power_kw"], 3.4)
        self.assertEqual(res["state"]["main"]["daily_consumption_kwh"], 102.5)

        status, r_res = app.handle_api("POST", "/api/rooms", {}, {"name": "Прихожая"}, token)
        self.assertEqual(status, 200)
        new_room_id = r_res["id"]

        status, d_res = app.handle_api(
            "POST",
            "/api/devices",
            {},
            {"room_id": new_room_id, "name": "Светильник", "device_type": "Датчик", "serial": "SN-999", "power_w": 60},
            token,
        )
        self.assertEqual(status, 200)
        self.assertGreater(d_res["id"], 0)

        status, _ = app.handle_api("POST", "/api/navigate", {}, {"target": "settings"}, token)
        self.assertEqual(status, 200)
        status, s_res = app.handle_api(
            "POST",
            "/api/settings",
            {},
            {"monthly_limit": 4500, "day_rate": 5.25, "night_rate": 2.05, "notify_80": False},
            token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(s_res["state"]["current_window"], "main")
        self.assertEqual(s_res["state"]["settings"]["monthly_budget_byn"], 4500.0)

        status, fam_res = app.handle_api(
            "POST", "/api/login", {}, {"login": DEMO_FAMILY_LOGIN, "password": DEMO_PASSWORD}, None
        )
        self.assertEqual(status, 200)
        fam_token = fam_res["token"]
        status, denied = app.handle_api("POST", "/api/navigate", {}, {"target": "settings"}, fam_token)
        self.assertEqual(status, 403)
        self.assertIn("error", denied)


if __name__ == "__main__":
    unittest.main()
