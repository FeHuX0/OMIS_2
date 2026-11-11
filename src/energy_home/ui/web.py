import json
import tempfile
import threading
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..automation import AutomationService
from ..demo import DEMO_PASSWORD, seed_demo
from ..domain import (
    LOCAL_TZ,
    STATE_LABELS,
    TRANSITIONS,
    AccessDenied,
    DomainError,
    ReadingPacket,
    Role,
    Session,
    ValidationError,
    now,
)
from ..extensions import BuildingService, HomeAssistantAdapter, VoiceAssistant
from ..gateways import SensorGateway, Simulator, import_csv

ACTION_LABELS = {
    "enable": "Включить питание",
    "start": "Начать работу",
    "finish": "Завершить цикл",
    "disable": "Выключить",
    "schedule": "Перенести на ночь",
    "reset": "Авария устранена: сбросить",
    "retire": "Списать устройство",
}

KIND_LABELS = {
    "meter": "Счётчик",
    "socket": "Умная розетка",
    "sensor": "Датчик",
}

TEMPLATE_PATH = Path(__file__).with_name("web_template.html")


class WebController:
    """Потокобезопасный контроллер веб-интерфейса поверх сервисов и SQLite."""

    def __init__(self, db_path: str | Path, *, demo: bool = False):
        self.db_path = db_path
        self.demo = demo
        self.lock = threading.RLock()
        from ..auth import AccessControl
        from ..reports import ReportService
        from ..services import EnergyService
        from ..storage import Database

        self.db = Database(db_path, check_same_thread=False)
        self.access = AccessControl(self.db)
        self.energy = EnergyService(self.db, self.access)
        self.reports = ReportService(self.db, self.access)
        self.automation = AutomationService(self.energy)
        self.building = BuildingService(self.db, self.access)
        self.assistant = VoiceAssistant(self.energy, self.reports, self.automation)
        self.gateway = SensorGateway(self.energy)
        self.simulator = Simulator(self.energy, self.gateway)
        if demo:
            seed_demo(self.energy)

    def _session(self, token: str | None) -> Session:
        if not token:
            raise AccessDenied("Сеанс завершён. Войдите снова.")
        return Session(token)

    def _serialize_device(self, d: dict) -> dict:
        actions = [
            {"id": act, "label": label}
            for act, label in ACTION_LABELS.items()
            if act in TRANSITIONS[d["state"]] and (act != "schedule" or d["flexible"])
        ]
        last_seen = d["last_seen"].strftime("%d.%m %H:%M:%S") if d["last_seen"] else "Нет показаний"
        if d["last_seen"] and not d["online"] and d["state"] != "emergency":
            last_seen += " · не на связи"
        return {
            "id": d["id"],
            "room_id": d["room_id"],
            "room": d["room"],
            "name": d["name"],
            "serial": d["serial"],
            "kind": d["kind"],
            "kind_label": KIND_LABELS[d["kind"]],
            "state": str(d["state"]),
            "state_label": STATE_LABELS[d["state"]],
            "rated_w": d["rated_w"],
            "critical_w": d["critical_w"],
            "power_w": d["power_w"],
            "flexible": bool(d["flexible"]),
            "essential": bool(d["essential"]),
            "counted": bool(d["counted"]),
            "online": bool(d["online"]),
            "last_seen": last_seen,
            "is_demo": d["serial"].startswith("DEMO-"),
            "actions": actions,
        }

    def state_payload(
        self,
        token: str | None,
        *,
        room_id: int | None = None,
        device_id: int | None = None,
        period: str = "month",
        anchor_str: str | None = None,
        common_kwh: float = 100.0,
        alloc_method: str = "area",
    ) -> dict:
        with self.lock:
            session = self._session(token)
            user = self.access.require(session)
            is_owner = user["role"] == Role.OWNER
            home = self.energy.home(session)
            rooms = self.energy.rooms(session)
            raw_devices = self.energy.devices(session)
            devices = [self._serialize_device(d) for d in raw_devices]
            visible = [
                d
                for d in raw_devices
                if (room_id is None or d["room_id"] == room_id)
                and (device_id is None or d["id"] == device_id)
            ]
            dashboard = self.reports.dashboard(session, room_id, device_id)
            full_dash = self.reports.dashboard(session)
            settings = self.energy.settings(session, owner=False)
            week_rep = self.reports.build(session, "week", room_id=room_id, device_id=device_id)

            anchor = now()
            if anchor_str:
                try:
                    parsed = datetime.strptime(anchor_str, "%Y-%m-%d")
                    anchor = datetime(parsed.year, parsed.month, parsed.day, tzinfo=LOCAL_TZ)
                except ValueError:
                    pass
            rep = self.reports.build(session, period, anchor, room_id, device_id)
            notifs = []
            for n in self.energy.notifications.list(session, 30):
                dt = datetime.fromisoformat(n["created_at"]).astimezone(LOCAL_TZ)
                notifs.append(
                    {
                        "id": n["id"],
                        "kind": n["kind"],
                        "message": n["message"],
                        "created_at": dt.strftime("%d.%m %H:%M"),
                        "is_read": bool(n["is_read"]),
                    }
                )
            family_rows = self.access.family(session) if is_owner else []
            apartments = self.building.distribute(session, common_kwh, alloc_method)
            power_kw = sum(d["power_w"] for d in visible if d["counted"]) / 1000.0
            budget_pct = (
                full_dash["monthly_cost"] / settings["monthly_budget"] * 100.0
                if settings["monthly_budget"]
                else 0.0
            )
            return {
                "user": {
                    "id": user["id"],
                    "name": user["name"],
                    "login": user["login"],
                    "role": str(user["role"]),
                    "role_label": "Владелец" if is_owner else "Член семьи",
                },
                "home": home,
                "rooms": rooms,
                "devices": devices,
                "metrics": {
                    "power_kw": round(power_kw, 2),
                    "daily_kwh": round(dashboard["daily_kwh"], 2),
                    "monthly_cost": round(dashboard["monthly_cost"], 2),
                    "full_monthly_cost": round(full_dash["monthly_cost"], 2),
                    "monthly_budget": round(settings["monthly_budget"], 2),
                    "budget_percent": round(budget_pct, 1),
                    "online_count": sum(1 for d in raw_devices if d["online"]),
                    "total_devices": len(raw_devices),
                    "updated_at": now().strftime("%H:%M:%S"),
                },
                "week_series": [{"label": lbl, "value": val} for lbl, val in week_rep.series],
                "notifications": notifs,
                "report": {
                    "start": rep.start.strftime("%d.%m.%Y %H:%M"),
                    "end": rep.end.strftime("%d.%m.%Y %H:%M"),
                    "energy_kwh": round(rep.energy_kwh, 2),
                    "cost": round(rep.cost, 2),
                    "previous_kwh": round(rep.previous_kwh, 2),
                    "change_percent": (
                        round(rep.change_percent, 1) if rep.change_percent is not None else None
                    ),
                    "series": [{"label": lbl, "value": val} for lbl, val in rep.series],
                    "devices": rep.devices,
                    "anomalies": rep.anomalies[:4],
                    "recommendations": rep.recommendations,
                },
                "settings": settings,
                "family": family_rows,
                "apartments": apartments,
            }


def make_handler(controller: WebController):
    html_bytes = TEMPLATE_PATH.read_bytes()

    class EnergyRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/export":
                qs = parse_qs(parsed.query)
                token = (qs.get("token") or [""])[0]
                kind = (qs.get("kind") or ["csv"])[0]
                period = (qs.get("period") or ["month"])[0]
                try:
                    with controller.lock:
                        session = controller._session(token)
                        report = controller.reports.build(session, period)
                        if kind == "pdf":
                            data = controller.reports.pdf_bytes(session, report)
                            user = controller.access.require(session)
                            controller.reports.record_export(user, report, "pdf")
                            mime = "application/pdf"
                            ext = "pdf"
                        else:
                            with tempfile.NamedTemporaryFile(
                                suffix=".csv", delete=False
                            ) as tmp:
                                tmp_path = Path(tmp.name)
                            controller.reports.export_csv(session, report, tmp_path)
                            data = tmp_path.read_bytes()
                            tmp_path.unlink(missing_ok=True)
                            mime = "text/csv; charset=utf-8"
                            ext = "csv"
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", mime)
                    self.send_header(
                        "Content-Disposition", f'attachment; filename="energy-report.{ext}"'
                    )
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except DomainError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                body = {}
            token = self.headers.get("X-Session-Token")
            path = urlparse(self.path).path

            try:
                with controller.lock:
                    if path == "/api/login":
                        session = controller.access.login(
                            body.get("login", ""), body.get("password", "")
                        )
                        self._send_json({"token": session.token})
                        return
                    if path == "/api/open-demo":
                        seed_demo(controller.energy)
                        session = controller.access.login("owner@demo.local", DEMO_PASSWORD)
                        controller.simulator.tick(session)
                        controller.gateway.drain()
                        self._send_json({"token": session.token})
                        return
                    if path == "/api/register":
                        session = controller.access.register_home(
                            body.get("name", ""),
                            body.get("login", ""),
                            body.get("password", ""),
                            body.get("home_name", ""),
                            body.get("address", ""),
                            body.get("kind", "apartment"),
                        )
                        self._send_json({"token": session.token})
                        return
                    if path == "/api/logout":
                        if token:
                            controller.access.logout(Session(token))
                        self._send_json({"ok": True})
                        return
                    if path == "/api/state":
                        state = controller.state_payload(
                            token,
                            room_id=body.get("room_id"),
                            device_id=body.get("device_id"),
                            period=body.get("period", "month"),
                            anchor_str=body.get("anchor"),
                            common_kwh=float(body.get("common_kwh", 100.0)),
                            alloc_method=body.get("alloc_method", "area"),
                        )
                        self._send_json(state)
                        return

                    session = controller._session(token)
                    if path == "/api/mark-read":
                        controller.energy.notifications.mark_read(session)
                        self._send_json({"ok": True})
                        return
                    if path == "/api/tick":
                        user = controller.access.require(session)
                        if user["role"] == Role.OWNER:
                            controller.automation.tick(session)
                            if body.get("simulate"):
                                controller.simulator.tick(session)
                            controller.gateway.drain()
                        self._send_json({"ok": True})
                        return
                    if path == "/api/add-room":
                        room_id = controller.energy.add_room(session, body.get("name", ""))
                        self._send_json({"id": room_id})
                        return
                    if path == "/api/add-device":
                        dev_id = controller.energy.add_device(
                            session,
                            int(body.get("room_id", 0)),
                            body.get("name", ""),
                            body.get("serial", ""),
                            body.get("kind", "socket"),
                            float(body.get("rated_w", 1000)),
                            float(body.get("critical_w", 3000)),
                            bool(body.get("flexible", False)),
                            bool(body.get("essential", False)),
                            bool(body.get("counted", True)),
                        )
                        self._send_json({"id": dev_id})
                        return
                    if path == "/api/device-state":
                        state = controller.energy.change_state(
                            session, int(body.get("device_id", 0)), body.get("action", "")
                        )
                        self._send_json({"state": str(state)})
                        return
                    if path == "/api/add-reading":
                        dev_id = int(body.get("device_id", 0))
                        seconds = int(body.get("interval_seconds", 60))
                        power = float(body.get("power_w", 100.0))
                        energy_kwh = float(body.get("energy_kwh", 0.0017))
                        last = controller.db.one(
                            "SELECT measured_at,interval_seconds FROM readings WHERE device_id=? "
                            "ORDER BY measured_at DESC LIMIT 1",
                            (dev_id,),
                        )
                        start = now() - timedelta(seconds=seconds)
                        if last:
                            start = max(
                                start,
                                datetime.fromisoformat(last["measured_at"])
                                + timedelta(seconds=last["interval_seconds"]),
                            )
                        controller.energy.ingest(
                            session, ReadingPacket(dev_id, start, power, energy_kwh, seconds)
                        )
                        self._send_json({"ok": True})
                        return
                    if path == "/api/test-anomaly":
                        d = controller.energy.device(session, int(body.get("device_id", 0)))
                        if not d["serial"].startswith("DEMO-"):
                            raise ValidationError(
                                "Проверка доступна только демонстрационным приборам."
                            )
                        if d["state"] not in ("idle", "running", "flexible"):
                            raise ValidationError(
                                "Включите демонстрационный прибор перед проверкой защиты."
                            )
                        last = controller.db.one(
                            "SELECT measured_at,interval_seconds FROM readings WHERE device_id=? "
                            "ORDER BY measured_at DESC LIMIT 1",
                            (d["id"],),
                        )
                        start = now() - timedelta(seconds=1)
                        if last:
                            start = max(
                                start,
                                datetime.fromisoformat(last["measured_at"])
                                + timedelta(seconds=last["interval_seconds"]),
                            )
                        power = d["critical_w"] * 1.5
                        controller.energy.ingest(
                            session, ReadingPacket(d["id"], start, power, power / 3_600_000, 1)
                        )
                        self._send_json({"ok": True})
                        return
                    if path == "/api/import-csv":
                        content = body.get("content", "").strip()
                        if not content:
                            raise ValidationError("Вставьте содержимое CSV.")
                        if not content.lower().startswith("serial"):
                            content = (
                                "serial;measured_at;power_w;energy_kwh;interval_seconds\n"
                                + content
                            )
                        with tempfile.NamedTemporaryFile(
                            "w", encoding="utf-8", suffix=".csv", delete=False
                        ) as tmp:
                            tmp.write(content)
                            tmp_path = Path(tmp.name)
                        try:
                            accepted, errors = import_csv(controller.energy, session, tmp_path)
                        finally:
                            tmp_path.unlink(missing_ok=True)
                        self._send_json({"accepted": accepted, "errors": errors})
                        return
                    if path == "/api/events":
                        rows = controller.energy.events(session)
                        formatted = [
                            {
                                "kind": r["kind"],
                                "message": r["message"],
                                "created_at": datetime.fromisoformat(r["created_at"])
                                .astimezone(LOCAL_TZ)
                                .strftime("%d.%m.%Y %H:%M:%S"),
                            }
                            for r in rows
                        ]
                        self._send_json({"events": formatted})
                        return
                    if path == "/api/save-settings":
                        controller.energy.save_settings(
                            session,
                            monthly_budget=float(body.get("monthly_budget", 100)),
                            daily_kwh=float(body.get("daily_kwh", 25)),
                            day_rate=float(body.get("day_rate", 0.28)),
                            night_rate=float(body.get("night_rate", 0.14)),
                            day_start=int(body.get("day_start", 7)),
                            night_start=int(body.get("night_start", 23)),
                            notify_80=bool(body.get("notify_80", True)),
                            auto_protect=bool(body.get("auto_protect", True)),
                        )
                        self._send_json({"ok": True})
                        return
                    if path == "/api/add-family":
                        controller.access.add_family(
                            session,
                            body.get("name", ""),
                            body.get("login", ""),
                            body.get("password", ""),
                        )
                        self._send_json({"ok": True})
                        return
                    if path == "/api/toggle-family":
                        controller.access.set_family_active(
                            session, int(body.get("user_id", 0)), bool(body.get("active", True))
                        )
                        self._send_json({"ok": True})
                        return
                    if path == "/api/apply-scenario":
                        changed = controller.automation.apply(session, body.get("scenario", ""))
                        self._send_json({"changed": changed})
                        return
                    if path == "/api/add-apartment":
                        controller.building.add_apartment(
                            session,
                            body.get("name", ""),
                            float(body.get("area", 50)),
                            int(body.get("residents", 1)),
                            float(body.get("own_kwh", 0)),
                        )
                        self._send_json({"ok": True})
                        return
                    if path == "/api/home-assistant":
                        controller.access.require(session, owner=True)
                        adapter = HomeAssistantAdapter(
                            body.get("url", ""), body.get("token", "")
                        )
                        power_w = adapter.power_w(body.get("entity", "").strip())
                        self._send_json({"power_w": power_w})
                        return
                    if path == "/api/assistant":
                        cmd = controller.assistant.recognize(body.get("text", ""))
                        reply = controller.assistant.execute(session, cmd)
                        self._send_json({"reply": reply})
                        return

                    self._send_json({"error": "Маршрут не найден."}, HTTPStatus.NOT_FOUND)
            except AccessDenied as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.FORBIDDEN)
            except (DomainError, ValueError, OSError) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    return EnergyRequestHandler


def run_web_app(
    db_path: str | Path,
    *,
    demo: bool = False,
    host: str = "0.0.0.0",
    port: int = 3000,
    open_browser: bool = False,
) -> int:
    controller = WebController(db_path, demo=demo)
    server = ThreadingHTTPServer((host, port), make_handler(controller))
    server.allow_reuse_address = True
    url = f"http://127.0.0.1:{port}"
    print(f"Energy Home запущен: {url}", flush=True)
    if open_browser:
        import webbrowser

        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.db.close()
    return 0
