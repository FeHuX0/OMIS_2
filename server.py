"""Energy Home HTTP Server (100% Python stdlib, port 3000)."""

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
    AccessDeniedError,
    DomainError,
    Reading,
    Role,
    User,
    ValidationError,
)
from energy_home.seed import create_system
from energy_home.state_machine import WindowState
from energy_home.subsystems import ApplicationInterface

ROOT_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT_DIR / "public"


class EnergyHomeApp:
    """Thread-safe web application controller over ApplicationInterface (Diagram 4.1)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.app_interface: ApplicationInterface = create_system()

    def _serialize_state(self, user: User, token: str, period: str = "week") -> dict[str, Any]:
        if period not in ("day", "week", "month"):
            period = "week"
        consumption = self.app_interface.show_consumption(user)
        report = self.app_interface.reporting.generate_report(period=period, home=user.home)
        tariff = self.app_interface.storage.active_tariff()
        limit = self.app_interface.storage.active_limit()
        window_fsm = self.app_interface.window_fsm(token)

        rooms_json = []
        if user.home is not None:
            for room in user.home.rooms:
                appliances_json = []
                for appliance in room.appliances:
                    appliances_json.append(
                        {
                            "id": appliance.id,
                            "name": appliance.name,
                            "power": appliance.power,
                            "current_power_w": appliance.current_power_w(),
                            "tree_label": appliance.tree_label(),
                            "device_type": appliance.metering_device.device_type if appliance.metering_device else "—",
                            "serial_number": appliance.metering_device.serial_number if appliance.metering_device else "—",
                            "outer_state": appliance.state_machine.outer_state.value,
                            "inner_state": (
                                appliance.state_machine.inner_state.value
                                if appliance.state_machine.inner_state
                                else None
                            ),
                            "available_actions": appliance.state_machine.available_actions(),
                        }
                    )
                rooms_json.append(
                    {
                        "id": room.id,
                        "name": room.name,
                        "collapsed_by_default": room.collapsed_by_default,
                        "appliances": appliances_json,
                    }
                )

        return {
            "current_window": window_fsm.current_window.value,
            "user": {
                "login": user.login,
                "name": user.name,
                "role": user.role.value,
                "role_label": user.role.display_label,
                "is_owner": user.role == Role.OWNER,
            },
            "main": {
                "current_power_kw": consumption["current_power_kw"],
                "daily_consumption_kwh": consumption["daily_consumption_kwh"],
                "alerts": consumption["alerts"],
            },
            "rooms": rooms_json,
            "report": {
                "period": report.period,
                "total_consumption_kwh": report.total_consumption_kwh,
                "total_cost_byn": report.total_cost_byn,
                "chart": {
                    "title": report.chart.title,
                    "subtitle": report.chart.subtitle,
                    "labels": report.chart.labels,
                    "values": report.chart.values,
                    "y_step": report.chart.y_step,
                    "y_max": report.chart.y_max,
                },
                "anomalies": [
                    {
                        "type": a.anomaly_type,
                        "magnitude": a.magnitude,
                        "location": a.location,
                        "description": a.describe(),
                    }
                    for a in report.anomalies
                ],
            },
            "settings": {
                "monthly_budget_byn": limit.monthly_budget_byn,
                "daily_limit_kwh": limit.daily_limit_kwh,
                "day_rate": tariff.day_rate,
                "night_rate": tariff.night_rate,
                "notify_at_80": limit.notify_at_80,
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
                    login = str(body.get("login", ""))
                    password = str(body.get("password", ""))
                    new_token, user = self.app_interface.access_control.login(login, password)
                    self.app_interface.window_fsm(new_token).current_window = WindowState.MAIN_WINDOW
                    return HTTPStatus.OK, {
                        "token": new_token,
                        "state": self._serialize_state(user, new_token),
                    }

                if method == "POST" and path == "/api/register":
                    login = str(body.get("login", ""))
                    password = str(body.get("password", ""))
                    name = str(body.get("name", ""))
                    role_str = str(body.get("role", Role.OWNER.value))
                    role = Role.FAMILY_MEMBER if role_str == Role.FAMILY_MEMBER.value else Role.OWNER
                    new_token, user = self.app_interface.access_control.register(
                        login=login,
                        password=password,
                        name=name,
                        role=role,
                    )
                    self.app_interface.window_fsm(new_token).current_window = WindowState.MAIN_WINDOW
                    return HTTPStatus.OK, {
                        "token": new_token,
                        "state": self._serialize_state(user, new_token),
                    }

                if method == "POST" and path == "/api/logout":
                    if token:
                        self.app_interface.window_fsm(token).current_window = WindowState.AUTH_WINDOW
                        self.app_interface.access_control.logout(token)
                    return HTTPStatus.OK, {"ok": True}

                user = self.app_interface.access_control.get_user(token)
                assert token is not None

                if method == "GET" and path == "/api/state":
                    period = (query.get("period") or ["week"])[0]
                    return HTTPStatus.OK, self._serialize_state(user, token, period)

                if method == "POST" and path == "/api/navigate":
                    target_str = str(body.get("target", ""))
                    try:
                        target = WindowState(target_str)
                    except ValueError as err:
                        raise ValidationError(f"Неизвестное окно: {target_str}") from err
                    fsm = self.app_interface.window_fsm(token)
                    try:
                        fsm.transition(target, is_owner=(user.role == Role.OWNER))
                    except PermissionError as err:
                        raise AccessDeniedError(str(err)) from err
                    except ValueError as err:
                        raise ValidationError(str(err)) from err
                    period = str(body.get("period", "week"))
                    return HTTPStatus.OK, self._serialize_state(user, token, period)

                if method == "POST" and path == "/api/rooms":
                    name = str(body.get("name", ""))
                    room = self.app_interface.add_room(user, name)
                    return HTTPStatus.OK, {
                        "id": room.id,
                        "state": self._serialize_state(user, token),
                    }

                if method == "POST" and path == "/api/devices":
                    try:
                        room_id = int(body.get("room_id", 0))
                        power_w = float(body.get("power_w", 0))
                    except (TypeError, ValueError) as err:
                        raise ValidationError("Укажите корректную комнату и мощность (число в Вт).") from err
                    appliance = self.app_interface.add_device(
                        user=user,
                        room_id=room_id,
                        name=str(body.get("name", "")),
                        device_type=str(body.get("device_type", "Умная розетка")),
                        serial_number=str(body.get("serial", "")),
                        power_w=power_w,
                    )
                    return HTTPStatus.OK, {
                        "id": appliance.id,
                        "state": self._serialize_state(user, token),
                    }

                if method == "POST" and path == "/api/device-state":
                    try:
                        device_id = int(body.get("device_id", 0))
                    except (TypeError, ValueError) as err:
                        raise ValidationError("Некорректный идентификатор устройства.") from err
                    action = str(body.get("action", ""))
                    self.app_interface.change_appliance_state(user, device_id, action)
                    return HTTPStatus.OK, {
                        "state": self._serialize_state(user, token),
                    }

                if method == "POST" and path == "/api/readings":
                    self.app_interface.access_control.require_permission(user, "manage_devices")
                    try:
                        device_id = int(body.get("device_id", 0))
                        power_w = float(body.get("power_w", 0))
                        energy_kwh = float(body.get("energy_kwh", 0))
                    except (TypeError, ValueError) as err:
                        raise ValidationError("Введите числовые значения мощности и энергии.") from err
                    home = user.home
                    if home is None:
                        raise ValidationError("Дом не найден.")
                    target = None
                    for room in home.rooms:
                        for appliance in room.appliances:
                            if appliance.id == device_id:
                                target = appliance
                                break
                    if target is None:
                        raise ValidationError("Прибор не найден.")
                    reading = Reading(
                        value=energy_kwh,
                        timestamp=datetime.now(LOCAL_TZ),
                        tariff=self.app_interface.storage.active_tariff(),
                        power_w=power_w,
                    )
                    result = self.app_interface.gateway.submit_reading(target, reading, home)
                    return HTTPStatus.OK, {
                        "result": result,
                        "state": self._serialize_state(user, token),
                    }

                if method == "POST" and path == "/api/settings":
                    try:
                        monthly_limit = float(body.get("monthly_limit"))
                        day_rate = float(body.get("day_rate"))
                        night_rate = float(body.get("night_rate"))
                    except (TypeError, ValueError) as err:
                        raise ValidationError("Введите числовые значения лимита и тарифов.") from err
                    notify_80 = bool(body.get("notify_80", True))
                    self.app_interface.configure_limit(
                        user=user,
                        monthly_budget_byn=monthly_limit,
                        day_rate=day_rate,
                        night_rate=night_rate,
                        notify_at_80=notify_80,
                    )
                    fsm = self.app_interface.window_fsm(token)
                    if fsm.current_window == WindowState.SETTINGS_WINDOW:
                        fsm.transition(WindowState.MAIN_WINDOW, is_owner=True)
                    return HTTPStatus.OK, {
                        "state": self._serialize_state(user, token),
                    }

                return HTTPStatus.NOT_FOUND, {"error": "Маршрут не найден."}
            except AccessDeniedError as err:
                return HTTPStatus.FORBIDDEN, {"error": str(err)}
            except DomainError as err:
                return HTTPStatus.BAD_REQUEST, {"error": str(err)}
            except Exception:
                return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Внутренняя ошибка сервера."}


APP = EnergyHomeApp()


class EnergyHomeRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler: API (/api/*) and static files (/public/*)."""

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
