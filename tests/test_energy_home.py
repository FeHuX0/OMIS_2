"""Модульные тесты Python-реализации Energy Home (диаграммы 4.1, 4.2.1–4.2.3, 5.1, 5.2 и API)."""

from __future__ import annotations

import unittest
from datetime import datetime

from energy_home.domain import (
    LOCAL_TZ,
    Аномалия,
    ДомКвартира,
    Комната,
    ОшибкаДоступа,
    Показание,
    Пользователь,
    Прибор,
    Роль,
    Событие,
    Тариф,
    УстройствоУчета,
)
from energy_home.extensions import (
    АдаптерGoogleHome,
    АдаптерHomeAssistant,
    АдаптерЯндексАлиса,
    ГолосовойАссистент,
    МногоквартирныйДом,
    МодульУправленияУстройствами,
    ОбщаяЗона,
    ПланировщикНагрузки,
    ПодсистемаПравил,
    Правило,
    ПравилоРаспределения,
    РейтингЭнергоэффективности,
    Сценарий,
)
from energy_home.seed import DEMO_FAMILY_LOGIN, DEMO_OWNER_LOGIN, DEMO_PASSWORD, создать_систему
from energy_home.state_machine import (
    АвтоматПереходовОкон,
    АвтоматСостоянийПрибора,
    СостояниеОкна,
    СостояниеПрибора,
)
from server import EnergyHomeApp


class TestBaseClassDiagram41(unittest.TestCase):
    """Проверка соответствия 1:1 базовой диаграмме классов 4.1 (стр. 13)."""

    def test_domain_classes_and_methods(self) -> None:
        тариф = Тариф(стоимостьДень=5.50, стоимостьНочь=2.10)
        устройство = УстройствоУчета(тип="Счётчик", серийныйНомер="SN-100", тариф=тариф, текущая_мощность_вт=1000.0)
        пок = устройство.считатьПоказание()
        self.assertIsInstance(пок, Показание)
        self.assertGreater(пок.значение, 0)

        прибор = Прибор(название="Холодильник", мощность=150.0, устройство_учета=устройство)
        комната = Комната(название="Кухня", приборы=[прибор])
        дом = ДомКвартира(адрес="Минск", комнаты=[комната])
        self.assertAlmostEqual(дом.получитьОбщееПотребление(), пок.значение, places=2)

        пользователь = Пользователь(логин="owner@test.by", пароль="secret", роль=Роль.ВЛАДЕЛЕЦ, дом=дом)
        self.assertTrue(пользователь.авторизоваться("secret"))
        self.assertFalse(пользователь.авторизоваться("wrong"))
        self.assertEqual(len(пользователь.события), 1)
        self.assertIsInstance(пользователь.события[0], Событие)

    def test_subsystems_zero_arg_and_full_calls(self) -> None:
        интерфейс = создать_систему()
        # Вызовы 1:1 без аргументов по диаграмме 4.1
        собранные = интерфейс.сбор.опроситьУстройства()
        self.assertGreaterEqual(len(собранные), 1)
        интерфейс.сбор.сохранитьПоказания()

        расход = интерфейс.анализ.рассчитатьРасход()
        self.assertGreater(расход, 0)
        аномалии = интерфейс.анализ.выявитьАномалии()
        self.assertGreaterEqual(len(аномалии), 1)

        интерфейс.уведомления.отправитьУведомление()
        self.assertGreaterEqual(len(интерфейс.хранение.найти("уведомления")), 2)

        отчет = интерфейс.отчетность.сформироватьОтчет()
        график = интерфейс.отчетность.построитьГрафик()
        self.assertEqual(отчет.график.метки, ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"])
        self.assertEqual(график.значения, [16.5, 13.3, 23.3, 15.1, 7.8, 0.0, 0.0])

        данные = интерфейс.показатьПотребление()
        self.assertIn("текущая_мощность_квт", данные)
        настройки = интерфейс.настроитьЛимит(месячный_лимит_руб=6000.0, стоимость_день=5.80)
        self.assertEqual(настройки["месячный_лимит_руб"], 6000.0)
        self.assertEqual(настройки["стоимость_день"], 5.80)

    def test_rbac_owner_vs_family(self) -> None:
        интерфейс = создать_систему()
        _, владелец = интерфейс.доступ.войти(DEMO_OWNER_LOGIN, DEMO_PASSWORD)
        _, член = интерфейс.доступ.войти(DEMO_FAMILY_LOGIN, DEMO_PASSWORD)

        self.assertTrue(интерфейс.доступ.проверитьПрава(владелец, "настройка_тарифов_и_лимитов"))
        self.assertFalse(интерфейс.доступ.проверитьПрава(член, "настройка_тарифов_и_лимитов"))
        self.assertTrue(интерфейс.доступ.проверитьПрава(член, "просмотр_отчетов"))

        with self.assertRaises(ОшибкаДоступа):
            интерфейс.настроитьЛимит(пользователь=член, месячный_лимит_руб=1000.0)


class TestStateMachines51And52(unittest.TestCase):
    """Проверка диаграмм состояний 5.1 и 5.2 (стр. 16–17)."""

    def test_device_lifecycle_51(self) -> None:
        fsm = АвтоматСостоянийПрибора()
        self.assertEqual(fsm.внешнее_состояние, СостояниеПрибора.ВЫКЛЮЧЕН)
        self.assertEqual(fsm.внутреннее_состояние, СостояниеПрибора.РЕЖИМ_ОБЕСТОЧИВАНИЯ)

        fsm.включить_прибор()
        self.assertEqual(fsm.текущее_состояние, СостояниеПрибора.ОЖИДАНИЕ_АКТИВНОСТИ)

        fsm.сигнал_включения()
        self.assertEqual(fsm.текущее_состояние, СостояниеПрибора.РАБОТА_В_ШТАТНОМ_РЕЖИМЕ)

        fsm.перейти_на_льготный_тариф()
        self.assertEqual(fsm.текущее_состояние, СостояниеПрибора.ГИБКАЯ_НАГРУЗКА)

        fsm.завершить_цикл_гибкой_нагрузки()
        self.assertEqual(fsm.текущее_состояние, СостояниеПрибора.РАБОТА_В_ШТАТНОМ_РЕЖИМЕ)

        уведомлено = False

        def cb() -> None:
            nonlocal уведомлено
            уведомлено = True

        fsm.событие_аномалии(превышен_критический_порог=True, уведомить=cb)
        self.assertTrue(уведомлено)
        self.assertEqual(fsm.внешнее_состояние, СостояниеПрибора.АВАРИЙНЫЙ_РЕЖИМ)
        self.assertEqual(fsm.внутреннее_состояние, СостояниеПрибора.ОТКЛЮЧЕНИЕ_ЛИНИИ)

        # Прямое включение из аварии запрещено — только сброс -> Выключен
        with self.assertRaises(ValueError):
            fsm.включить_прибор()

        fsm.устранение_аварии()
        self.assertEqual(fsm.внешнее_состояние, СостояниеПрибора.ВЫКЛЮЧЕН)

    def test_window_transitions_52(self) -> None:
        nav = АвтоматПереходовОкон()
        self.assertEqual(nav.текущее_окно, СостояниеОкна.ОКНО_АВТОРИЗАЦИИ)

        nav.перейти(СостояниеОкна.ГЛАВНОЕ_ОКНО)
        with self.assertRaises(PermissionError):
            nav.перейти(СостояниеОкна.ОКНО_НАСТРОЕК, роль_владелец=False)

        nav.перейти(СостояниеОкна.ОКНО_УСТРОЙСТВ)
        # Напрямую из ОкноУстройств в ОкноОтчетов нельзя (только через ГлавноеОкно)
        with self.assertRaises(ValueError):
            nav.перейти(СостояниеОкна.ОКНО_ОТЧЕТОВ)

        nav.перейти(СостояниеОкна.ГЛАВНОЕ_ОКНО)
        nav.перейти(СостояниеОкна.ОКНО_НАСТРОЕК, роль_владелец=True)
        nav.перейти(СостояниеОкна.ГЛАВНОЕ_ОКНО)
        nav.перейти(СостояниеОкна.ОКНО_АВТОРИЗАЦИИ)


class TestExtensions42(unittest.TestCase):
    """Проверка 3 направлений развития 4.2.1, 4.2.2, 4.2.3 (стр. 14–15)."""

    def test_voice_and_smart_home_421(self) -> None:
        for adapter in (АдаптерЯндексАлиса(), АдаптерGoogleHome(), АдаптерHomeAssistant()):
            adapter.передатьКоманду("Кондиционер", "off")
            ev = adapter.получитьСобытия()
            self.assertIsInstance(ev, Событие)
        ассистент = ГолосовойАссистент()
        cmd = ассистент.распознатьКоманду("Текущее потребление")
        self.assertEqual(cmd.намерение, "потребление")
        ассистент.озвучитьОтвет("3.4 кВт")
        self.assertEqual(ассистент.последний_ответ, "3.4 кВт")

    def test_apartment_building_422(self) -> None:
        дом = МногоквартирныйДом(
            адрес="пр. Независимости, 10",
            общие_зоны=[ОбщаяЗона("Подъезд", 35.0), ОбщаяЗона("Лифт", 45.0), ОбщаяЗона("Парковка", 20.0)],
            квартиры=[ДомКвартира("кв. 1", базовое_потребление_сегодня_квтч=100.0)],
            правило_распределения=ПравилоРаспределения("по_площади"),
        )
        self.assertEqual(дом.получитьОбщийРасход(), 200.0)
        доли = дом.правило_распределения.распределить(100.0, {"кв. 1": 60.0, "кв. 2": 40.0})
        self.assertEqual(доли["кв. 1"], 60.0)
        self.assertEqual(доли["кв. 2"], 40.0)
        рейтинг = РейтингЭнергоэффективности(расход_квартиры=80.0, средний_расход_по_дому=100.0)
        self.assertEqual(рейтинг.сравнитьСоСредним(), 20.0)
        self.assertEqual(рейтинг.балл, 70.0)

    def test_smart_control_423(self) -> None:
        прибор = Прибор(название="Стиральная машина", мощность=2000.0, гибкая_нагрузка=True)
        модуль = МодульУправленияУстройствами()
        модуль.включить(прибор)
        self.assertEqual(прибор.автомат.текущее_состояние, СостояниеПрибора.РАБОТА_В_ШТАТНОМ_РЕЖИМЕ)

        планировщик = ПланировщикНагрузки(модуль)
        планировщик.перенестиНагрузку(прибор, "23:00")
        self.assertEqual(прибор.автомат.текущее_состояние, СостояниеПрибора.ГИБКАЯ_НАГРУЗКА)

        сценарий = Сценарий(имя="Отпуск", приборы=[прибор], модуль_управления=модуль)
        сценарий.выполнить()
        self.assertEqual(прибор.автомат.внешнее_состояние, СостояниеПрибора.ВЫКЛЮЧЕН)

        подсистема = ПодсистемаПравил(правила=[Правило(условие="скачок", действие="отключить")])
        ан = Аномалия(тип="Скачок мощности", величина=23.0, время=datetime(2027, 3, 20, 2, 34, tzinfo=LOCAL_TZ))
        self.assertTrue(подсистема.проверитьПравило(ан))


class TestHttpApiFlow(unittest.TestCase):
    """Интеграционный тест REST API контроллера EnergyHomeApp."""

    def test_full_api_journey(self) -> None:
        app = EnergyHomeApp()

        # Неверный пароль
        status, err = app.handle_api("POST", "/api/login", {}, {"login": DEMO_OWNER_LOGIN, "password": "bad"}, None)
        self.assertEqual(status, 403)
        self.assertIn("error", err)

        # Вход владельца
        status, res = app.handle_api(
            "POST", "/api/login", {}, {"login": DEMO_OWNER_LOGIN, "password": DEMO_PASSWORD}, None
        )
        self.assertEqual(status, 200)
        token = res["token"]
        self.assertEqual(res["state"]["главное"]["текущая_мощность_квт"], 3.4)
        self.assertEqual(res["state"]["главное"]["расход_за_сегодня_квтч"], 102.5)

        # Добавление помещения и устройства
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

        # Переход в настройки и сохранение
        status, _ = app.handle_api("POST", "/api/navigate", {}, {"target": "ОкноНастроек"}, token)
        self.assertEqual(status, 200)
        status, s_res = app.handle_api(
            "POST",
            "/api/settings",
            {},
            {"monthly_limit": 4500, "day_rate": 5.25, "night_rate": 2.05, "notify_80": False},
            token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(s_res["state"]["текущее_окно"], "ГлавноеОкно")
        self.assertEqual(s_res["state"]["настройки"]["месячный_лимит_руб"], 4500.0)

        # Проверка RBAC для члена семьи
        status, fam_res = app.handle_api(
            "POST", "/api/login", {}, {"login": DEMO_FAMILY_LOGIN, "password": DEMO_PASSWORD}, None
        )
        self.assertEqual(status, 200)
        fam_token = fam_res["token"]
        status, denied = app.handle_api("POST", "/api/navigate", {}, {"target": "ОкноНастроек"}, fam_token)
        self.assertEqual(status, 403)
        self.assertIn("error", denied)


if __name__ == "__main__":
    unittest.main()
