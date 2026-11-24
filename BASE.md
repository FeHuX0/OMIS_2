# Базовая диаграмма классов (раздел 4.1, стр. 13 `1laba_full_fc613401.pdf`)

Документ описывает **1:1 соответствие** между русскоязычными сущностями базовой диаграммы классов из отчёта (стр. 13) и их **англоязычной реализацией в Python** (по PEP 8): какой класс за что отвечает, где он находится в коде, какие у него атрибуты, методы и связи.

---

## 1. Сводная карта классов

### Пакет «Ядро системы (Домен)»

| Класс на диаграмме 4.1 (PDF) | Тип / Стереотип | Класс в Python | Файл |
|---|---|---|---|
| `Пользователь` | Class | `User` | `energy_home/domain.py` |
| `Роль` (`ВЛАДЕЛЕЦ`, `ЧЛЕН_СЕМЬИ`) | Enum (`E`) | `Role` (`OWNER`, `FAMILY_MEMBER`) | `energy_home/domain.py` |
| `Событие` | Class | `Event` | `energy_home/domain.py` |
| `Дом / Квартира` | Class | `Home` | `energy_home/domain.py` |
| `Комната` | Class | `Room` | `energy_home/domain.py` |
| `Прибор` | Class | `Appliance` | `energy_home/domain.py` |
| `Устройство учета` | Class | `MeteringDevice` | `energy_home/domain.py` |
| `Показание` | Class | `Reading` | `energy_home/domain.py` |
| `Тариф` | Class | `Tariff` | `energy_home/domain.py` |

### Пакет «Подсистемы»

| Подсистема на диаграмме 4.1 (PDF) | Стереотип | Класс в Python | Файл |
|---|---|---|---|
| `Интерфейс приложения` | `<<boundary>>` | `ApplicationInterface` | `energy_home/subsystems.py` |
| `Подсистема отчётности` | `<<boundary>>` | `ReportingSubsystem` | `energy_home/subsystems.py` |
| `Подсистема уведомлений` | `<<control>>` | `NotificationSubsystem` | `energy_home/subsystems.py` |
| `Подсистема управления доступом` | `<<control>>` | `AccessControlSubsystem` | `energy_home/subsystems.py` |
| `Подсистема сбора данных` | `<<control>>` | `DataCollectionSubsystem` | `energy_home/subsystems.py` |
| `Подсистема анализа` | `<<control>>` | `AnalysisSubsystem` | `energy_home/subsystems.py` |
| `Подсистема хранения` | `<<infrastructure>>` | `StorageSubsystem` | `energy_home/subsystems.py` |

### Возвращаемые и сопутствующие типы (из методов 4.1 / ER / 4.2.3)

| Сущность в отчёте | Класс в Python | Файл | Назначение |
|---|---|---|---|
| `Отчет` | `Report` | `energy_home/domain.py` | Возвращаемое значение `ReportingSubsystem.generate_report()` |
| `Chart` | `Chart` | `energy_home/domain.py` | Возвращаемое значение `ReportingSubsystem.build_chart()` |
| `Аномалия` | `Anomaly` | `energy_home/domain.py` | Элементы списка из `AnalysisSubsystem.detect_anomalies()` |
| `Лимит` | `Limit` | `energy_home/domain.py` | Настраивается через `ApplicationInterface.configure_limit()` |
| `Уведомление` | `Notification` | `energy_home/domain.py` | Создаётся в `NotificationSubsystem.send_notification()` |

---

## 2. Подробный разбор: Пакет «Ядро системы (Домен)»

Все доменные классы объявлены в **`energy_home/domain.py`** и реэкспортируются из **`energy_home/__init__.py`**.

### 2.1. `Role` (`Роль`, Enum)
- **Файл:** `energy_home/domain.py` (`class Role(str, Enum)`)
- **Значения:**
  - `Role.OWNER` ↔ `ВЛАДЕЛЕЦ`
  - `Role.FAMILY_MEMBER` ↔ `ЧЛЕН_СЕМЬИ`
- **Где используется:**
  - Поле `User.role`.
  - Проверка прав в `AccessControlSubsystem.check_permissions()` (`energy_home/subsystems.py`).
  - Охранное условие `[Роль == Владелец]` при переходе в `WindowState.SETTINGS_WINDOW` (`energy_home/state_machine.py` и `server.py`).

### 2.2. `User` (`Пользователь`)
- **Файл:** `energy_home/domain.py` (`class User`)
- **Атрибуты с диаграммы:**
  - `login: str` ↔ `- логин: String`
  - `password: str` ↔ `- пароль: String` *(хранится как PBKDF2-SHA256 хеш)*
- **Связи с диаграммы:**
  - `1 — имеет — 1 Роль` → `role: Role`
  - `1 — владеет — 1 Дом / Квартира` → `home: Optional[Home]`
  - `1 — инициирует — * Событие` → `events: list[Event]`
- **Методы с диаграммы:**
  - `authenticate(candidate_password: Optional[str] = None) -> bool` ↔ `+ авторизоваться(): Boolean`
- **Где используется:** `AccessControlSubsystem.login()`, `register()`, `energy_home/seed.py`.

### 2.3. `Event` (`Событие`)
- **Файл:** `energy_home/domain.py` (`class Event`)
- **Атрибуты с диаграммы:**
  - `event_type: str` ↔ `- тип: String`
  - `timestamp: datetime` ↔ `- дата: DateTime`
- **Дополнительные поля:** `description: str`, `user_login: Optional[str]`.
- **Где используется:** журнал событий при входе/выходе, регистрации, ошибках авторизации, изменении настроек, добавлении комнат/приборов и смене состояний (`energy_home/subsystems.py`, `StorageSubsystem.events`).

### 2.4. `Home` (`Дом / Квартира`)
- **Файл:** `energy_home/domain.py` (`class Home`)
- **Атрибуты с диаграммы:**
  - `address: str` ↔ `- адрес: String`
- **Связи с диаграммы:**
  - `1 — содержит (композиция) — * Комната` → `rooms: list[Room]`
- **Методы с диаграммы:**
  - `get_total_consumption() -> float` ↔ `+ получитьОбщееПотребление(): Double`
- **Вспомогательные методы:** `current_power_kw()`, `add_room()`.
- **Где используется:** `ApplicationInterface.show_consumption()`, `AnalysisSubsystem.calculate_consumption()`, `energy_home/seed.py`.

### 2.5. `Room` (`Комната`)
- **Файл:** `energy_home/domain.py` (`class Room`)
- **Атрибуты с диаграммы:**
  - `name: str` ↔ `- название: String`
- **Связи с диаграммы:**
  - `1 — содержит (композиция) — * Прибор` → `appliances: list[Appliance]`
- **Где используется:** дерево помещений (`Кухня`, `Спальня`, `Зал`, `Санузел`) в окне устройств, добавление комнат через `ApplicationInterface.add_room()` и `POST /api/rooms` (`server.py`).

### 2.6. `Appliance` (`Прибор`)
- **Файл:** `energy_home/domain.py` (`class Appliance`)
- **Атрибуты с диаграммы:**
  - `name: str` ↔ `- название: String`
  - `power: float` ↔ `- мощность: Double`
- **Связи с диаграммы:**
  - `1 — оснащен — 1 Устройство учета` → `metering_device: Optional[MeteringDevice]`
- **Интеграция с диаграммой состояний 5.1:**
  - Поле `state_machine: DeviceStateMachine` (`energy_home/state_machine.py`) управляет жизненным циклом (`OFF`, `ON` [`IDLE_WAITING`, `NORMAL_OPERATION`, `FLEXIBLE_LOAD`], `EMERGENCY_MODE` [`ANOMALY_DETECTED`, `LINE_DISCONNECTED`], `RETIRED`).
- **Где используется:** `ApplicationInterface.add_device()`, `change_appliance_state()`, `POST /api/devices`, `POST /api/device-state`.

### 2.7. `MeteringDevice` (`Устройство учета`)
- **Файл:** `energy_home/domain.py` (`class MeteringDevice`)
- **Атрибуты с диаграммы:**
  - `device_type: str` ↔ `- тип: String`
  - `serial_number: str` ↔ `- серийныйНомер: String`
- **Связи с диаграммы:**
  - `1 — фиксирует — * Показание` → `readings: list[Reading]`
- **Методы с диаграммы:**
  - `read_measurement(value=None, timestamp=None, power_w=None) -> Reading` ↔ `+ считатьПоказание(): Показание`
- **Где используется:** `DataCollectionSubsystem.poll_devices()`, `SensorGateway.submit_reading()`.

### 2.8. `Reading` (`Показание`)
- **Файл:** `energy_home/domain.py` (`class Reading`)
- **Атрибуты с диаграммы:**
  - `value: float` ↔ `- значение: Double`
  - `timestamp: datetime` ↔ `- время: DateTime`
- **Связи с диаграммы:**
  - `1 — рассчитывается по — 1 Тариф` → `tariff: Optional[Tariff]`
- **Вспомогательный метод:** `cost() -> float`.
- **Где используется:** `DataCollectionSubsystem`, `StorageSubsystem.readings`, `AnalysisSubsystem`, `SensorGateway` (`POST /api/readings`).

### 2.9. `Tariff` (`Тариф`)
- **Файл:** `energy_home/domain.py` (`class Tariff`)
- **Атрибуты с диаграммы:**
  - `day_rate: float` ↔ `- стоимостьДень: Double` *(по умолчанию `5.50`)*
  - `night_rate: float` ↔ `- стоимостьНочь: Double` *(по умолчанию `2.10`)*
- **Вспомогательный метод:** `rate_for_time(timestamp: datetime) -> float`.
- **Где используется:** `Reading`, `StorageSubsystem.tariffs`, `ApplicationInterface.configure_limit()`, `POST /api/settings`.

---

## 3. Подробный разбор: Пакет «Подсистемы»

Все 7 подсистем диаграммы 4.1 объявлены в **`energy_home/subsystems.py`** и собираются в **`create_system()`** (`energy_home/seed.py`).

### 3.1. `StorageSubsystem` (`Подсистема хранения`, `<<infrastructure>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `save(obj: Any) -> None` ↔ `+ сохранить(объект): void`
  - `find(criteria: Any) -> list[Any]` ↔ `+ найти(критерий): List`
- **Зависимости (`..>`):** хранит `Event`, `Reading`, `Tariff` (и остальные доменные сущности).

### 3.2. `AccessControlSubsystem` (`Подсистема управления доступом`, `<<control>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `check_permissions(user: Optional[User], action: str) -> bool` ↔ `+ проверитьПрава(пользователь, действие): Boolean`
- **Дополнительные методы:** `login()`, `logout()`, `get_user()`, `register()`, `require_permission()`.
- **Зависимости (`..>`):** проверяет `User`, использует `Role`.
- **Где вызывается:** на всех защищённых маршрутах в `server.py`.

### 3.3. `DataCollectionSubsystem` (`Подсистема сбора данных`, `<<control>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `poll_devices(home=None) -> list[Reading]` ↔ `+ опроситьУстройства(): List`
  - `save_readings(readings=None) -> None` ↔ `+ сохранитьПоказания(): void`
- **Зависимости (`..>`):** опрашивает `MeteringDevice`, передаёт данные в `StorageSubsystem`.

### 3.4. `AnalysisSubsystem` (`Подсистема анализа`, `<<control>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `calculate_consumption(home=None, period=None) -> float` ↔ `+ рассчитатьРасход(): Double`
  - `detect_anomalies(home=None, spike_multiplier=1.2) -> list[Anomaly]` ↔ `+ выявитьАномалии(): List`
- **Дополнительный метод:** `check_limits(home)` (дневной лимит и 80%/100% месячного лимита).
- **Зависимости (`..>`):** читает данные из `StorageSubsystem`.

### 3.5. `NotificationSubsystem` (`Подсистема уведомлений`, `<<control>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `send_notification(message=None, notification_type="warning") -> None` ↔ `+ отправитьУведомление(): void`
- **Зависимости (`..>`):** получает аномалии из `AnalysisSubsystem` и сохраняет `Notification` в `StorageSubsystem`.

### 3.6. `ReportingSubsystem` (`Подсистема отчётности`, `<<boundary>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `generate_report(period="week", home=None) -> Report` ↔ `+ сформироватьОтчет(): Отчет`
  - `build_chart(period="week") -> Chart` ↔ `+ построитьГрафик(): Chart`
- **Зависимости (`..>`):** использует результаты `AnalysisSubsystem`.
- **Где используется:** `GET /api/state?period=...` и экран отчётов (`public/app.js` → `drawReportChart()`).

### 3.7. `ApplicationInterface` (`Интерфейс приложения`, `<<boundary>>`)
- **Файл:** `energy_home/subsystems.py`
- **Методы с диаграммы:**
  - `show_consumption(user=None) -> dict` ↔ `+ показатьПотребление(): void`
  - `configure_limit(user=None, monthly_budget_byn=None, day_rate=None, night_rate=None, notify_at_80=None) -> dict` ↔ `+ настроитьЛимит(): void`
- **Дополнительные методы:** `add_room()`, `add_device()`, `change_appliance_state()`, `window_fsm()`.
- **Зависимости (`..>`):**
  - запрашивает отчёты у `ReportingSubsystem` (`self.reporting`);
  - отображает данные `DataCollectionSubsystem` (`self.collection`);
  - делегирует проверку `AccessControlSubsystem` (`self.access_control`);
  - взаимодействует с `User`.

---

## 4. Как всё соединяется при запуске

1. **`server.py` (`EnergyHomeApp.__init__`)** вызывает **`create_system()`** из **`energy_home/seed.py`**.
2. **`energy_home/seed.py`** создаёт все 7 подсистем диаграммы 4.1 (`StorageSubsystem`, `AccessControlSubsystem`, `DataCollectionSubsystem`, `AnalysisSubsystem`, `NotificationSubsystem`, `ReportingSubsystem`, `ApplicationInterface`) и заполняет `StorageSubsystem` начальными объектами домена (`Tariff`, `Limit`, `Home`, 4 `Room`, `Appliance` + `MeteringDevice`, 2 `User`, `Anomaly`, `Notification`, `Event`).
3. **`server.py` (`EnergyHomeRequestHandler`)** принимает HTTP-запросы от веб-интерфейса (`public/index.html`, `public/app.js`) на порту `3000` и вызывает методы `ApplicationInterface` и подсистем.
4. **`tests/test_energy_home.py`** (`TestBaseClassDiagram41`) проверяет все классы, атрибуты, методы и правила RBAC.
