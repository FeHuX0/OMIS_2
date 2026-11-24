"""HTTP-сервер Energy Home (100% Python stdlib, порт 3000)."""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from energy_home.domain import (
    LOCAL_TZ,
    ОшибкаВалидации,
    ОшибкаДомена,
    ОшибкаДоступа,
    Показание,
    Пользователь,
    Роль,
)
from energy_home.seed import создать_систему
from energy_home.state_machine import СостояниеОкна
from energy_home.subsystems import ИнтерфейсПриложения

ROOT_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT_DIR / "public"


class EnergyHomeApp:
    """Потокобезопасный контроллер веб-приложения поверх ИнтерфейсПриложения (4.1)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.интерфейс: ИнтерфейсПриложения = создать_систему()

    def _сериализовать_состояние(self, пользователь: Пользователь, токен: str, период: str = "week") -> dict[str, Any]:
        if период not in ("day", "week", "month"):
            период = "week"
        потребление = self.интерфейс.показатьПотребление(пользователь)
        отчет = self.интерфейс.отчетность.сформироватьОтчет(период=период, дом=пользователь.дом)
        тариф = self.интерфейс.хранение.активный_тариф()
        лимит = self.интерфейс.хранение.активный_лимит()
        автомат_окон = self.интерфейс.автомат_окон(токен)

        комнаты_json = []
        if пользователь.дом is not None:
            for комната in пользователь.дом.комнаты:
                приборы_json = []
                for прибор in комната.приборы:
                    приборы_json.append(
                        {
                            "id": прибор.id,
                            "название": прибор.название,
                            "мощность": прибор.мощность,
                            "текущая_мощность_вт": прибор.текущая_мощность_вт(),
                            "строка": прибор.строка_для_дерева(),
                            "тип_устройства": прибор.устройство_учета.тип if прибор.устройство_учета else "—",
                            "серийный_номер": прибор.устройство_учета.серийныйНомер if прибор.устройство_учета else "—",
                            "внешнее_состояние": прибор.автомат.внешнее_состояние.value,
                            "внутреннее_состояние": (
                                прибор.автомат.внутреннее_состояние.value
                                if прибор.автомат.внутреннее_состояние
                                else None
                            ),
                            "доступные_действия": прибор.автомат.доступные_действия(),
                        }
                    )
                комнаты_json.append(
                    {
                        "id": комната.id,
                        "название": комната.название,
                        "свернута_по_умолчанию": комната.свернута_по_умолчанию,
                        "приборы": приборы_json,
                    }
                )

        return {
            "текущее_окно": автомат_окон.текущее_окно.value,
            "пользователь": {
                "логин": пользователь.логин,
                "имя": пользователь.имя,
                "роль": пользователь.роль.value,
                "роль_подпись": пользователь.роль.отображаемое_название,
                "владелец": пользователь.роль == Роль.ВЛАДЕЛЕЦ,
            },
            "главное": {
                "текущая_мощность_квт": потребление["текущая_мощность_квт"],
                "расход_за_сегодня_квтч": потребление["расход_за_сегодня_квтч"],
                "предупреждения": потребление["предупреждения"],
            },
            "помещения": комнаты_json,
            "отчет": {
                "период": отчет.период,
                "общий_расход_квтч": отчет.общий_расход_квтч,
                "общая_стоимость_руб": отчет.общая_стоимость_руб,
                "график": {
                    "заголовок": отчет.график.заголовок,
                    "подзаголовок": отчет.график.подзаголовок,
                    "метки": отчет.график.метки,
                    "значения": отчет.график.значения,
                    "шаг_оси_y": отчет.график.шаг_оси_y,
                    "максимум_оси_y": отчет.график.максимум_оси_y,
                },
                "аномалии": [
                    {
                        "тип": a.тип,
                        "величина": a.величина,
                        "место": a.место,
                        "описание": a.описать(),
                    }
                    for a in отчет.аномалии
                ],
            },
            "настройки": {
                "месячный_лимит_руб": лимит.месячный_лимит_руб,
                "дневной_лимит_квтч": лимит.дневной_лимит_квтч,
                "стоимость_день": тариф.стоимостьДень,
                "стоимость_ночь": тариф.стоимостьНочь,
                "уведомлять_при_80": лимит.уведомлять_при_80,
            },
        }

    def handle_api(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: dict[str, Any],
        token: Optional[str],
    ) -> tuple[int, dict[str, Any]]:
        with self._lock:
            try:
                if method == "POST" and path == "/api/login":
                    логин = str(body.get("login", ""))
                    пароль = str(body.get("password", ""))
                    новый_токен, пользователь = self.интерфейс.доступ.войти(логин, пароль)
                    self.интерфейс.автомат_окон(новый_токен).текущее_окно = СостояниеОкна.ГЛАВНОЕ_ОКНО
                    return HTTPStatus.OK, {
                        "token": новый_токен,
                        "state": self._сериализовать_состояние(пользователь, новый_токен),
                    }

                if method == "POST" and path == "/api/register":
                    логин = str(body.get("login", ""))
                    пароль = str(body.get("password", ""))
                    имя = str(body.get("name", ""))
                    роль_стр = str(body.get("role", Роль.ВЛАДЕЛЕЦ.value))
                    роль = Роль.ЧЛЕН_СЕМЬИ if роль_стр == Роль.ЧЛЕН_СЕМЬИ.value else Роль.ВЛАДЕЛЕЦ
                    новый_токен, пользователь = self.интерфейс.доступ.зарегистрировать(
                        логин=логин,
                        пароль=пароль,
                        имя=имя,
                        роль=роль,
                    )
                    self.интерфейс.автомат_окон(новый_токен).текущее_окно = СостояниеОкна.ГЛАВНОЕ_ОКНО
                    return HTTPStatus.OK, {
                        "token": новый_токен,
                        "state": self._сериализовать_состояние(пользователь, новый_токен),
                    }

                if method == "POST" and path == "/api/logout":
                    if token:
                        self.интерфейс.автомат_окон(token).текущее_окно = СостояниеОкна.ОКНО_АВТОРИЗАЦИИ
                        self.интерфейс.доступ.выйти(token)
                    return HTTPStatus.OK, {"ok": True}

                пользователь = self.интерфейс.доступ.получить_пользователя(token)
                assert token is not None

                if method == "GET" and path == "/api/state":
                    период = (query.get("period") or ["week"])[0]
                    return HTTPStatus.OK, self._сериализовать_состояние(пользователь, token, период)

                if method == "POST" and path == "/api/navigate":
                    цель_стр = str(body.get("target", ""))
                    try:
                        цель = СостояниеОкна(цель_стр)
                    except ValueError as err:
                        raise ОшибкаВалидации(f"Неизвестное окно: {цель_стр}") from err
                    автомат = self.интерфейс.автомат_окон(token)
                    try:
                        автомат.перейти(цель, роль_владелец=(пользователь.роль == Роль.ВЛАДЕЛЕЦ))
                    except PermissionError as err:
                        raise ОшибкаДоступа(str(err)) from err
                    except ValueError as err:
                        raise ОшибкаВалидации(str(err)) from err
                    период = str(body.get("period", "week"))
                    return HTTPStatus.OK, self._сериализовать_состояние(пользователь, token, период)

                if method == "POST" and path == "/api/rooms":
                    название = str(body.get("name", ""))
                    комната = self.интерфейс.добавить_помещение(пользователь, название)
                    return HTTPStatus.OK, {
                        "id": комната.id,
                        "state": self._сериализовать_состояние(пользователь, token),
                    }

                if method == "POST" and path == "/api/devices":
                    try:
                        id_комнаты = int(body.get("room_id", 0))
                        мощность_вт = float(body.get("power_w", 0))
                    except (TypeError, ValueError) as err:
                        raise ОшибкаВалидации("Укажите корректную комнату и мощность (число в Вт).") from err
                    прибор = self.интерфейс.добавить_устройство(
                        пользователь=пользователь,
                        id_комнаты=id_комнаты,
                        название=str(body.get("name", "")),
                        тип_устройства=str(body.get("device_type", "Умная розетка")),
                        серийный_номер=str(body.get("serial", "")),
                        мощность_вт=мощность_вт,
                    )
                    return HTTPStatus.OK, {
                        "id": прибор.id,
                        "state": self._сериализовать_состояние(пользователь, token),
                    }

                if method == "POST" and path == "/api/device-state":
                    try:
                        id_прибора = int(body.get("device_id", 0))
                    except (TypeError, ValueError) as err:
                        raise ОшибкаВалидации("Некорректный идентификатор устройства.") from err
                    действие = str(body.get("action", ""))
                    self.интерфейс.изменить_состояние_прибора(пользователь, id_прибора, действие)
                    return HTTPStatus.OK, {
                        "state": self._сериализовать_состояние(пользователь, token),
                    }

                if method == "POST" and path == "/api/readings":
                    self.интерфейс.доступ.требовать_права(пользователь, "управление_устройствами")
                    try:
                        id_прибора = int(body.get("device_id", 0))
                        мощность_вт = float(body.get("power_w", 0))
                        значение_квтч = float(body.get("energy_kwh", 0))
                    except (TypeError, ValueError) as err:
                        raise ОшибкаВалидации("Введите числовые значения мощности и энергии.") from err
                    дом = пользователь.дом
                    if дом is None:
                        raise ОшибкаВалидации("Дом не найден.")
                    целевой = None
                    for комната in дом.комнаты:
                        for прибор in комната.приборы:
                            if прибор.id == id_прибора:
                                целевой = прибор
                                break
                    if целевой is None:
                        raise ОшибкаВалидации("Прибор не найден.")
                    пок = Показание(
                        значение=значение_квтч,
                        время=datetime.now(LOCAL_TZ),
                        тариф=self.интерфейс.хранение.активный_тариф(),
                        мощность_вт=мощность_вт,
                    )
                    рез = self.интерфейс.шлюз.передать_показание(целевой, пок, дом)
                    return HTTPStatus.OK, {
                        "result": рез,
                        "state": self._сериализовать_состояние(пользователь, token),
                    }

                if method == "POST" and path == "/api/settings":
                    try:
                        месячный_лимит = float(body.get("monthly_limit"))
                        стоимость_день = float(body.get("day_rate"))
                        стоимость_ночь = float(body.get("night_rate"))
                    except (TypeError, ValueError) as err:
                        raise ОшибкаВалидации("Введите числовые значения лимита и тарифов.") from err
                    уведомлять_80 = bool(body.get("notify_80", True))
                    self.интерфейс.настроитьЛимит(
                        пользователь=пользователь,
                        месячный_лимит_руб=месячный_лимит,
                        стоимость_день=стоимость_день,
                        стоимость_ночь=стоимость_ночь,
                        уведомлять_при_80=уведомлять_80,
                    )
                    # По диаграмме 5.2 кнопка «Сохранить и закрыть» возвращает в ГлавноеОкно
                    автомат = self.интерфейс.автомат_окон(token)
                    if автомат.текущее_окно == СостояниеОкна.ОКНО_НАСТРОЕК:
                        автомат.перейти(СостояниеОкна.ГЛАВНОЕ_ОКНО, роль_владелец=True)
                    return HTTPStatus.OK, {
                        "state": self._сериализовать_состояние(пользователь, token),
                    }

                return HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден."}
            except ОшибкаДоступа as err:
                return HTTPStatus.FORBIDDEN, {"error": str(err)}
            except ОшибкаДомена as err:
                return HTTPStatus.BAD_REQUEST, {"error": str(err)}
            except Exception:
                return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Внутренняя ошибка сервера."}


APP = EnergyHomeApp()


class EnergyHomeRequestHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов: API (/api/*) и статика (/public/*)."""

    server_version = "EnergyHomePython/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _extract_token(self) -> Optional[str]:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip() or None
        return self.headers.get("X-Session-Token") or None

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        length_raw = self.headers.get("Content-Length", "0")
        try:
            length = int(length_raw)
        except ValueError:
            return {}
        if length <= 0 or length > 1_048_576:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _serve_static(self, req_path: str) -> None:
        clean = req_path.split("?", 1)[0].lstrip("/")
        if not clean:
            clean = "index.html"
        target = (PUBLIC_DIR / clean).resolve()
        public_root = PUBLIC_DIR.resolve()
        if not target.is_relative_to(public_root) or not target.is_file():
            # SPA fallback на index.html только для не-API путей без расширения
            if "." not in Path(clean).name:
                target = (public_root / "index.html").resolve()
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return
        content = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        content_type = mime or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            status, payload = APP.handle_api(
                "GET",
                parsed.path,
                parse_qs(parsed.query),
                {},
                self._extract_token(),
            )
            self._send_json(status, payload)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            body = self._read_json_body()
            status, payload = APP.handle_api(
                "POST",
                parsed.path,
                parse_qs(parsed.query),
                body,
                self._extract_token(),
            )
            self._send_json(status, payload)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")


def run_server() -> None:
    host = os.environ.get("HOST") or "0.0.0.0"
    port = int(os.environ.get("PORT") or "3000")
    server = ThreadingHTTPServer((host, port), EnergyHomeRequestHandler)
    print(f"Energy Home (Python) запущен: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
