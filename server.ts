import express, { Request, Response } from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { AccessControl } from "./src/auth.js";
import { AutomationService } from "./src/automation.js";
import { DEMO_PASSWORD, seedDemo } from "./src/demo.js";
import {
  ACTION_LABELS,
  AccessDenied,
  DeviceKind,
  DomainError,
  KIND_LABELS,
  ReadingPacket,
  Role,
  STATE_LABELS,
  Session,
  TRANSITIONS,
  ValidationError,
  formatLocalDateTime,
  formatLocalShortDateTime,
  formatLocalShortDateTimeSec,
  formatLocalTime,
  formatLocalReportRange,
  localToUtcMs,
  nowMs,
} from "./src/domain.js";
import {
  BuildingService,
  GoogleHomeAdapter,
  HomeAssistantAdapter,
  SmartHomeAdapter,
  VoiceAssistant,
  YandexAliceAdapter,
} from "./src/extensions.js";
import { SensorGateway, Simulator, importCsvContent } from "./src/gateways.js";
import { ReportService } from "./src/reports.js";
import { DeviceView, EnergyService } from "./src/services.js";
import { MemoryStore } from "./src/store.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, "public");

export class WebController {
  readonly store = new MemoryStore();
  readonly access = new AccessControl(this.store);
  readonly energy = new EnergyService(this.store, this.access);
  readonly reports = new ReportService(this.energy);
  readonly automation = new AutomationService(this.energy);
  readonly building = new BuildingService(this.energy);
  readonly assistant = new VoiceAssistant(this.energy, this.reports, this.automation);
  readonly gateway = new SensorGateway(this.energy);
  readonly simulator = new Simulator(this.energy, this.gateway);

  constructor(demo = true) {
    if (demo) {
      seedDemo(this.energy);
    }
  }

  requireSession(token: string | undefined): Session {
    if (!token) {
      throw new AccessDenied("Сеанс завершён. Войдите снова.");
    }
    return { token };
  }

  private serializeDevice(d: DeviceView) {
    const allowedTransitions = TRANSITIONS[d.state] ?? {};
    const actions = Object.entries(ACTION_LABELS)
      .filter(([act]) => act in allowedTransitions && (act !== "schedule" || d.flexible))
      .map(([id, label]) => ({ id, label }));

    let lastSeen = d.last_seen_ms ? formatLocalShortDateTimeSec(d.last_seen_ms) : "Нет показаний";
    if (d.last_seen_ms && !d.online && d.state !== "emergency") {
      lastSeen += " · не на связи";
    }
    return {
      id: d.id,
      room_id: d.room_id,
      room: d.room,
      name: d.name,
      serial: d.serial,
      kind: d.kind as DeviceKind,
      kind_label: KIND_LABELS[d.kind],
      state: String(d.state),
      state_label: STATE_LABELS[d.state],
      rated_w: d.rated_w,
      critical_w: d.critical_w,
      power_w: d.power_w,
      flexible: d.flexible,
      essential: d.essential,
      counted: d.counted,
      online: d.online,
      last_seen: lastSeen,
      is_demo: d.serial.startsWith("DEMO-"),
      actions,
    };
  }

  statePayload(
    token: string | undefined,
    opts: {
      room_id?: number | null;
      device_id?: number | null;
      period?: string;
      anchor?: string | null;
      common_kwh?: number | null;
      alloc_method?: string;
    } = {}
  ) {
    const session = this.requireSession(token);
    const user = this.access.require(session);
    const isOwner = user.role === Role.OWNER;
    const home = this.energy.home(session);
    const rooms = this.energy.rooms(session);
    const rawDevices = this.energy.devices(session);
    const devices = rawDevices.map((d) => this.serializeDevice(d));
    const roomId = opts.room_id ?? null;
    const deviceId = opts.device_id ?? null;

    const visible = rawDevices.filter(
      (d) => (roomId === null || d.room_id === roomId) && (deviceId === null || d.id === deviceId)
    );
    const dashboard = this.reports.dashboard(session, roomId, deviceId);
    const fullDash = this.reports.dashboard(session);
    const settings = this.energy.settings(session, false);
    const weekRep = this.reports.build(session, "week", null, roomId, deviceId);

    let anchorMs: number | null = null;
    if (opts.anchor) {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(opts.anchor.trim());
      if (m) {
        anchorMs = localToUtcMs(Number(m[1]), Number(m[2]), Number(m[3]), 0, 0, 0, 0);
      }
    }
    const rep = this.reports.build(session, opts.period || "month", anchorMs, roomId, deviceId);

    const notifs = this.energy.notifications.list(session, 30).map((n) => ({
      id: n.id,
      kind: n.kind,
      message: n.message,
      created_at: formatLocalShortDateTime(n.created_at_ms),
      is_read: n.is_read,
    }));

    const familyRows = isOwner ? this.access.family(session) : [];
    const commonZones = this.building.commonZones(session);
    const totalZonesKwh = commonZones.reduce((acc, z) => acc + z.kwh, 0);
    const effectiveCommonKwh =
      opts.common_kwh !== undefined && opts.common_kwh !== null && Number.isFinite(opts.common_kwh)
        ? opts.common_kwh
        : totalZonesKwh;
    const apartments = this.building.distribute(session, effectiveCommonKwh, opts.alloc_method || "area");
    const rules = this.automation.rules(session);

    const powerKw = visible.filter((d) => d.counted).reduce((acc, d) => acc + d.power_w, 0) / 1000.0;
    const budgetPct = settings.monthly_budget ? (fullDash.monthly_cost / settings.monthly_budget) * 100.0 : 0.0;

    // Active alerts for Home screen banner (5.3.2)
    const alerts: string[] = [];
    if (fullDash.daily_kwh >= settings.daily_kwh) {
      alerts.push(
        `Внимание! Превышен лимит дневного потребления энергии (${fullDash.daily_kwh.toFixed(2)} / ${settings.daily_kwh.toFixed(2)} кВт·ч).`
      );
    }
    if (fullDash.monthly_cost >= settings.monthly_budget) {
      alerts.push(
        `Внимание! Превышен месячный лимит расходов (${fullDash.monthly_cost.toFixed(2)} / ${settings.monthly_budget.toFixed(2)} BYN).`
      );
    } else if (settings.notify_80 && fullDash.monthly_cost >= settings.monthly_budget * 0.8) {
      alerts.push(
        `Внимание! Достигнуто ${Math.round(budgetPct)}% месячного лимита расходов.`
      );
    }
    const emergencyDevs = rawDevices.filter((d) => d.state === "emergency");
    if (emergencyDevs.length > 0) {
      alerts.push(
        `Аварийное обесточивание: ${emergencyDevs.map((d) => d.name).join(", ")}.`
      );
    }

    return {
      user: {
        id: user.id,
        name: user.name,
        login: user.login,
        role: String(user.role),
        role_label: isOwner ? "Владелец дома" : "Член семьи",
      },
      home,
      rooms,
      devices,
      alerts,
      metrics: {
        power_kw: Number(powerKw.toFixed(2)),
        daily_kwh: Number(dashboard.daily_kwh.toFixed(2)),
        monthly_cost: Number(dashboard.monthly_cost.toFixed(2)),
        full_monthly_cost: Number(fullDash.monthly_cost.toFixed(2)),
        monthly_budget: Number(settings.monthly_budget.toFixed(2)),
        budget_percent: Number(budgetPct.toFixed(1)),
        online_count: rawDevices.filter((d) => d.online).length,
        total_devices: rawDevices.length,
        updated_at: formatLocalTime(nowMs()),
      },
      week_series: weekRep.series.map(([label, value]) => ({ label, value })),
      notifications: notifs,
      report: {
        start: formatLocalReportRange(rep.start_ms),
        end: formatLocalReportRange(rep.end_ms),
        energy_kwh: Number(rep.energy_kwh.toFixed(2)),
        cost: Number(rep.cost.toFixed(2)),
        previous_kwh: Number(rep.previous_kwh.toFixed(2)),
        change_percent: rep.change_percent !== null ? Number(rep.change_percent.toFixed(1)) : null,
        series: rep.series.map(([label, value]) => ({ label, value })),
        devices: rep.devices,
        anomalies: rep.anomalies.slice(0, 4).map((a) => ({
          ...a,
          created_at: formatLocalReportRange(a.created_at_ms),
        })),
        recommendations: rep.recommendations,
      },
      settings,
      family: familyRows,
      common_zones: commonZones,
      total_common_kwh: Number(totalZonesKwh.toFixed(2)),
      apartments,
      rules,
    };
  }
}

const controller = new WebController(true);
const app = express();
app.use(express.json({ limit: "2mb" }));

app.get("/api/export", (req: Request, res: Response) => {
  try {
    const token = String(req.query.token ?? "");
    const kind = String(req.query.kind ?? "csv");
    const period = String(req.query.period ?? "month");
    const anchorStr = String(req.query.anchor ?? "");
    let anchorMs: number | null = null;
    if (anchorStr) {
      const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(anchorStr.trim());
      if (m) {
        anchorMs = localToUtcMs(Number(m[1]), Number(m[2]), Number(m[3]), 0, 0, 0, 0);
      }
    }
    const session = controller.requireSession(token);
    const report = controller.reports.build(session, period, anchorMs);
    if (kind === "pdf") {
      const data = controller.reports.pdfBytes(session, report);
      res.setHeader("Content-Type", "application/pdf");
      res.setHeader("Content-Disposition", 'attachment; filename="energy-report.pdf"');
      res.send(data);
    } else {
      const data = controller.reports.csvBytes(session, report);
      res.setHeader("Content-Type", "text/csv; charset=utf-8");
      res.setHeader("Content-Disposition", 'attachment; filename="energy-report.csv"');
      res.send(data);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Ошибка экспорта";
    res.status(400).json({ error: msg });
  }
});

app.post("/api/:action", async (req: Request, res: Response) => {
  const action = req.params.action;
  const body = (req.body ?? {}) as Record<string, unknown>;
  const token = req.header("X-Session-Token") ?? undefined;

  try {
    if (action === "login") {
      const session = controller.access.login(String(body.login ?? ""), String(body.password ?? ""));
      res.json({ token: session.token });
      return;
    }
    if (action === "open-demo") {
      seedDemo(controller.energy);
      const session = controller.access.login("owner@demo.local", DEMO_PASSWORD);
      controller.simulator.tick(session);
      controller.gateway.drain();
      res.json({ token: session.token });
      return;
    }
    if (action === "register") {
      const session = controller.access.registerHome(
        String(body.name ?? ""),
        String(body.login ?? ""),
        String(body.password ?? ""),
        String(body.home_name ?? ""),
        String(body.address ?? ""),
        String(body.kind ?? "apartment")
      );
      res.json({ token: session.token });
      return;
    }
    if (action === "logout") {
      if (token) controller.access.logout({ token });
      res.json({ ok: true });
      return;
    }
    if (action === "state") {
      const payload = controller.statePayload(token, {
        room_id: body.room_id !== null && body.room_id !== undefined ? Number(body.room_id) : null,
        device_id: body.device_id !== null && body.device_id !== undefined ? Number(body.device_id) : null,
        period: String(body.period ?? "month"),
        anchor: body.anchor ? String(body.anchor) : null,
        common_kwh:
          body.common_kwh !== undefined && body.common_kwh !== null && body.common_kwh !== ""
            ? Number(body.common_kwh)
            : null,
        alloc_method: String(body.alloc_method ?? "area"),
      });
      res.json(payload);
      return;
    }

    const session = controller.requireSession(token);

    if (action === "mark-read") {
      controller.energy.notifications.markRead(session);
      res.json({ ok: true });
      return;
    }
    if (action === "tick") {
      const user = controller.access.require(session);
      if (user.role === Role.OWNER) {
        controller.automation.tick(session);
        if (body.simulate) {
          controller.simulator.tick(session);
        }
        controller.gateway.drain();
      }
      res.json({ ok: true });
      return;
    }
    if (action === "add-room") {
      const id = controller.energy.addRoom(session, String(body.name ?? ""));
      res.json({ id });
      return;
    }
    if (action === "add-device") {
      const id = controller.energy.addDevice(
        session,
        Number(body.room_id ?? 0),
        String(body.name ?? ""),
        String(body.serial ?? ""),
        String(body.kind ?? "socket"),
        Number(body.rated_w ?? 1000),
        Number(body.critical_w ?? 3000),
        Boolean(body.flexible),
        Boolean(body.essential),
        body.counted !== undefined ? Boolean(body.counted) : true
      );
      res.json({ id });
      return;
    }
    if (action === "device-state") {
      const state = controller.energy.changeState(
        session,
        Number(body.device_id ?? 0),
        String(body.action ?? "")
      );
      res.json({ state: String(state) });
      return;
    }
    if (action === "add-reading") {
      const devId = Number(body.device_id ?? 0);
      const seconds = Number(body.interval_seconds ?? 60);
      const power = Number(body.power_w ?? 100);
      const energyKwh = Number(body.energy_kwh ?? 0.0017);
      const list = controller.store.readingsByDevice.get(devId);
      const last = list && list.length > 0 ? list[list.length - 1] : undefined;
      let startMs = nowMs() - seconds * 1000;
      if (last) {
        startMs = Math.max(startMs, last.measured_at_ms + last.interval_seconds * 1000);
      }
      const packet: ReadingPacket = {
        device_id: devId,
        measured_at_ms: startMs,
        power_w: power,
        energy_kwh: energyKwh,
        interval_seconds: seconds,
      };
      controller.energy.ingest(session, packet);
      res.json({ ok: true });
      return;
    }
    if (action === "test-anomaly") {
      const d = controller.energy.device(session, Number(body.device_id ?? 0));
      if (!d.serial.startsWith("DEMO-")) {
        throw new ValidationError("Проверка доступна только демонстрационным приборам.");
      }
      if (d.state !== "idle" && d.state !== "running" && d.state !== "flexible") {
        throw new ValidationError("Включите демонстрационный прибор перед проверкой защиты.");
      }
      const list = controller.store.readingsByDevice.get(d.id);
      const last = list && list.length > 0 ? list[list.length - 1] : undefined;
      let startMs = nowMs() - 1000;
      if (last) {
        startMs = Math.max(startMs, last.measured_at_ms + last.interval_seconds * 1000);
      }
      const power = d.critical_w * 1.5;
      controller.energy.ingest(session, {
        device_id: d.id,
        measured_at_ms: startMs,
        power_w: power,
        energy_kwh: power / 3_600_000,
        interval_seconds: 1,
      });
      res.json({ ok: true });
      return;
    }
    if (action === "import-csv") {
      let content = String(body.content ?? "").trim();
      if (!content) {
        throw new ValidationError("Вставьте содержимое CSV.");
      }
      if (!content.toLowerCase().startsWith("serial")) {
        content = `serial;measured_at;power_w;energy_kwh;interval_seconds\n${content}`;
      }
      const result = importCsvContent(controller.energy, session, content);
      res.json(result);
      return;
    }
    if (action === "events") {
      const rows = controller.energy.events(session);
      res.json({
        events: rows.map((r) => ({
          kind: r.kind,
          message: r.message,
          created_at: formatLocalDateTime(r.created_at_ms),
        })),
      });
      return;
    }
    if (action === "save-settings") {
      controller.energy.saveSettings(session, {
        monthly_budget: Number(body.monthly_budget ?? 100),
        daily_kwh: Number(body.daily_kwh ?? 25),
        day_rate: Number(body.day_rate ?? 0.28),
        night_rate: Number(body.night_rate ?? 0.14),
        day_start: Number(body.day_start ?? 7),
        night_start: Number(body.night_start ?? 23),
        notify_80: Boolean(body.notify_80 ?? true),
        auto_protect: Boolean(body.auto_protect ?? true),
      });
      res.json({ ok: true });
      return;
    }
    if (action === "add-family") {
      controller.access.addFamily(
        session,
        String(body.name ?? ""),
        String(body.login ?? ""),
        String(body.password ?? "")
      );
      res.json({ ok: true });
      return;
    }
    if (action === "toggle-family") {
      controller.access.setFamilyActive(session, Number(body.user_id ?? 0), Boolean(body.active ?? true));
      res.json({ ok: true });
      return;
    }
    if (action === "apply-scenario") {
      const changed = controller.automation.apply(session, String(body.scenario ?? ""));
      res.json({ changed });
      return;
    }
    if (action === "reschedule-load") {
      const state = controller.automation.scheduler.rescheduleLoad(
        session,
        Number(body.device_id ?? 0),
        String(body.target_time ?? "ночной тариф")
      );
      res.json({ state: String(state) });
      return;
    }
    if (action === "add-rule") {
      const rule = controller.automation.addRule(
        session,
        String(body.condition ?? ""),
        String(body.action ?? "")
      );
      res.json({ rule });
      return;
    }
    if (action === "check-rule") {
      const matched = controller.automation.checkRule(session, String(body.event_type ?? ""));
      res.json({ matched });
      return;
    }
    if (action === "add-apartment") {
      controller.building.addApartment(
        session,
        String(body.name ?? ""),
        Number(body.area ?? 50),
        Number(body.residents ?? 1),
        Number(body.own_kwh ?? 0)
      );
      res.json({ ok: true });
      return;
    }
    if (action === "add-common-zone") {
      const zone = controller.building.addCommonZone(
        session,
        String(body.zone_type ?? ""),
        Number(body.kwh ?? 0)
      );
      res.json({ zone });
      return;
    }
    if (action === "home-assistant") {
      controller.access.require(session, true);
      const adapter = new HomeAssistantAdapter(String(body.url ?? ""), String(body.token ?? ""));
      const powerW = await adapter.powerW(String(body.entity ?? "").trim());
      res.json({ power_w: powerW });
      return;
    }
    if (action === "smart-home-command") {
      controller.access.require(session, true);
      const platform = String(body.platform ?? "home_assistant");
      const targetDevice = String(body.entity ?? "switch.living_room");
      const cmd = String(body.command ?? "off");
      let adapter: SmartHomeAdapter;
      if (platform === "yandex_alice") {
        adapter = new YandexAliceAdapter();
      } else if (platform === "google_home") {
        adapter = new GoogleHomeAdapter();
      } else {
        adapter = new HomeAssistantAdapter(String(body.url ?? ""), String(body.token ?? ""));
      }
      const reply = await adapter.sendCommand(targetDevice, cmd);
      const status = await adapter.getEvent();
      res.json({ reply, status });
      return;
    }
    if (action === "assistant") {
      const cmd = controller.assistant.recognize(String(body.text ?? ""));
      const reply = controller.assistant.execute(session, cmd);
      res.json({ reply, intent: cmd.intent });
      return;
    }

    res.status(404).json({ error: "Маршрут не найден." });
  } catch (err) {
    if (err instanceof AccessDenied) {
      res.status(403).json({ error: err.message });
      return;
    }
    if (err instanceof DomainError) {
      res.status(400).json({ error: err.message });
      return;
    }
    res.status(400).json({ error: err instanceof Error ? err.message : "Ошибка запроса" });
  }
});

app.use(express.static(PUBLIC_DIR));
app.use((_req: Request, res: Response) => {
  res.sendFile(path.join(PUBLIC_DIR, "index.html"));
});

const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || "0.0.0.0";

app.listen(port, host, () => {
  console.log(`Energy Home запущен: http://${host}:${port}`);
});
