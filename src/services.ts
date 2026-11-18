import { AccessControl } from "./auth.js";
import {
  DeviceKind,
  DeviceState,
  ReadingPacket,
  Session,
  ValidationError,
  getLocalParts,
  nowMs,
  startOfLocalDayMs,
  startOfLocalHourMs,
  transition,
} from "./domain.js";
import { DeviceRow, MemoryStore, ReadingRow, ReadingSegmentRow, TariffRow } from "./store.js";

export function numberValue(value: unknown, name: string, minimum = 0, maximum = 1_000_000): number {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") {
    throw new ValidationError(`${name}: введите число.`);
  }
  const result = Number(value);
  if (!Number.isFinite(result) || result < minimum || result > maximum) {
    throw new ValidationError(`${name}: допустимо от ${minimum} до ${maximum}.`);
  }
  return result;
}

export interface DeviceView {
  id: number;
  appliance_id: number;
  room_id: number;
  room: string;
  home_id: number;
  name: string;
  serial: string;
  kind: DeviceKind;
  state: DeviceState;
  rated_w: number;
  critical_w: number;
  flexible: boolean;
  essential: boolean;
  counted: boolean;
  last_seen_ms: number | null;
  online: boolean;
  power_w: number;
}

export interface PriceSegment {
  start_ms: number;
  end_ms: number;
  energy_kwh: number;
  cost: number;
  tariff_id: number;
}

export class NotificationService {
  constructor(
    private readonly store: MemoryStore,
    private readonly access: AccessControl
  ) {}

  send(homeId: number, kind: string, message: string, key: string | null = null): void {
    if (key !== null) {
      const composite = `${homeId}:${key}`;
      if (this.store.notificationDedupeKeys.has(composite)) {
        return;
      }
      this.store.notificationDedupeKeys.add(composite);
    }
    this.store.notifications.push({
      id: this.store.nextId("notification"),
      home_id: homeId,
      kind,
      message,
      created_at_ms: nowMs(),
      dedupe_key: key,
    });
  }

  list(session: Session, limit = 100): Array<{
    id: number;
    home_id: number;
    kind: string;
    message: string;
    created_at_ms: number;
    is_read: boolean;
  }> {
    const user = this.access.require(session);
    const items = [];
    for (let i = this.store.notifications.length - 1; i >= 0 && items.length < limit; i--) {
      const n = this.store.notifications[i];
      if (n.home_id === user.home_id) {
        items.push({
          id: n.id,
          home_id: n.home_id,
          kind: n.kind,
          message: n.message,
          created_at_ms: n.created_at_ms,
          is_read: this.store.notificationReads.has(`${n.id}:${user.id}`),
        });
      }
    }
    return items;
  }

  markRead(session: Session): void {
    const user = this.access.require(session);
    for (const n of this.store.notifications) {
      if (n.home_id === user.home_id) {
        this.store.notificationReads.add(`${n.id}:${user.id}`);
      }
    }
  }
}

export class EnergyService {
  readonly notifications: NotificationService;

  constructor(
    readonly store: MemoryStore,
    readonly access: AccessControl
  ) {
    this.notifications = new NotificationService(store, access);
  }

  home(session: Session) {
    const user = this.access.require(session);
    const h = this.store.homes.get(user.home_id);
    if (!h) throw new ValidationError("Дом не найден.");
    return h;
  }

  rooms(session: Session) {
    const user = this.access.require(session);
    const rows = [];
    for (const r of this.store.rooms.values()) {
      if (r.home_id === user.home_id) rows.push(r);
    }
    return rows.sort((a, b) => a.id - b.id);
  }

  addRoom(session: Session, name: string): number {
    const user = this.access.require(session, true);
    const clean = (name ?? "").trim();
    if (!clean || clean.length > 100) {
      throw new ValidationError("Название комнаты должно содержать от 1 до 100 символов.");
    }
    const lower = clean.toLowerCase();
    for (const r of this.store.rooms.values()) {
      if (r.home_id === user.home_id && r.name.toLowerCase() === lower) {
        throw new ValidationError("Комната с таким названием уже существует.");
      }
    }
    const id = this.store.nextId("room");
    this.store.rooms.set(id, { id, home_id: user.home_id, name: clean });
    this.store.logEvent(user.home_id, "room_added", `Добавлена комната: ${clean}`);
    return id;
  }

  private buildDeviceView(device: DeviceRow): DeviceView | null {
    const appliance = this.store.appliances.get(device.appliance_id);
    if (!appliance) return null;
    const room = this.store.rooms.get(appliance.room_id);
    if (!room) return null;
    const readings = this.store.readingsByDevice.get(device.id);
    const last = readings && readings.length > 0 ? readings[readings.length - 1] : undefined;
    const endMs = last ? last.measured_at_ms + last.interval_seconds * 1000 : null;
    const online = Boolean(endMs !== null && nowMs() - endMs <= 300_000);
    const powerW =
      last && online && (device.state === DeviceState.RUNNING || device.state === DeviceState.IDLE)
        ? last.power_w
        : 0;
    return {
      id: device.id,
      appliance_id: device.appliance_id,
      room_id: room.id,
      room: room.name,
      home_id: room.home_id,
      name: appliance.name,
      serial: device.serial,
      kind: device.kind,
      state: device.state,
      rated_w: appliance.rated_w,
      critical_w: device.critical_w,
      flexible: appliance.flexible,
      essential: appliance.essential,
      counted: device.counted,
      last_seen_ms: endMs,
      online,
      power_w: powerW,
    };
  }

  devices(session: Session, includeRetired = false): DeviceView[] {
    const user = this.access.require(session);
    const result: DeviceView[] = [];
    for (const d of this.store.devices.values()) {
      if (!includeRetired && d.state === DeviceState.RETIRED) continue;
      const view = this.buildDeviceView(d);
      if (view && view.home_id === user.home_id) {
        result.push(view);
      }
    }
    return result.sort((a, b) => a.room_id - b.room_id || a.id - b.id);
  }

  device(session: Session, deviceId: number, owner = false): DeviceView {
    const user = this.access.require(session, owner);
    const raw = this.store.devices.get(deviceId);
    const view = raw ? this.buildDeviceView(raw) : null;
    if (!view || view.home_id !== user.home_id) {
      throw new ValidationError("Устройство не найдено в вашем доме.");
    }
    return view;
  }

  addDevice(
    session: Session,
    roomId: number,
    name: string,
    serial: string,
    kind: string,
    ratedW: number,
    criticalW: number,
    flexible = false,
    essential = false,
    counted = true
  ): number {
    const user = this.access.require(session, true);
    const room = this.store.rooms.get(roomId);
    if (!room || room.home_id !== user.home_id) {
      throw new ValidationError("Сначала выберите комнату вашего дома.");
    }
    const cleanRated = numberValue(ratedW, "Номинальная мощность", 1);
    const cleanCritical = numberValue(criticalW, "Критический порог", cleanRated);
    const cleanName = (name ?? "").trim();
    const cleanSerial = (serial ?? "").trim();
    if (!cleanName || !cleanSerial || Math.max(cleanName.length, cleanSerial.length) > 100) {
      throw new ValidationError("Укажите название и серийный номер длиной до 100 символов.");
    }
    if (kind !== "meter" && kind !== "socket" && kind !== "sensor") {
      throw new ValidationError("Неизвестный тип устройства учёта.");
    }
    if (this.store.devicesBySerial.has(cleanSerial.toLowerCase())) {
      throw new ValidationError("Устройство с таким серийным номером уже существует.");
    }
    const applianceId = this.store.nextId("appliance");
    this.store.appliances.set(applianceId, {
      id: applianceId,
      room_id: roomId,
      name: cleanName,
      rated_w: cleanRated,
      flexible: Boolean(flexible),
      essential: Boolean(essential),
    });

    const deviceId = this.store.nextId("device");
    const devRow: DeviceRow = {
      id: deviceId,
      appliance_id: applianceId,
      serial: cleanSerial,
      kind,
      state: DeviceState.IDLE,
      critical_w: cleanCritical,
      counted: Boolean(counted),
      created_at_ms: nowMs(),
    };
    this.store.devices.set(deviceId, devRow);
    this.store.devicesBySerial.set(cleanSerial.toLowerCase(), devRow);
    this.store.logEvent(user.home_id, "device_added", `Зарегистрирован прибор: ${cleanName}`, {
      device_id: deviceId,
    });
    return deviceId;
  }

  changeState(session: Session, deviceId: number, action: string): DeviceState {
    const view = this.device(session, deviceId, true);
    if (action === "schedule" && !view.flexible) {
      throw new ValidationError("Этот прибор не поддерживает перенос нагрузки.");
    }
    const nextState = transition(view.state, action);
    const raw = this.store.devices.get(deviceId)!;
    raw.state = nextState;
    this.store.logEvent(view.home_id, "state_changed", `${view.name}: ${nextState}`, {
      device_id: deviceId,
    });
    return nextState;
  }

  getActiveTariff(homeId: number, atMs: number): TariffRow | null {
    let best: TariffRow | null = null;
    for (const t of this.store.tariffs.values()) {
      if (t.home_id === homeId && t.effective_at_ms <= atMs) {
        if (!best || t.effective_at_ms > best.effective_at_ms || (t.effective_at_ms === best.effective_at_ms && t.id > best.id)) {
          best = t;
        }
      }
    }
    return best;
  }

  settings(session: Session, owner = true) {
    const user = this.access.require(session, owner);
    const s = this.store.settings.get(user.home_id);
    const tariff = this.getActiveTariff(user.home_id, nowMs());
    if (!s || !tariff) throw new ValidationError("Настройки дома не найдены.");
    return {
      ...s,
      day_rate: tariff.day_rate,
      night_rate: tariff.night_rate,
      day_start: tariff.day_start,
      night_start: tariff.night_start,
    };
  }

  saveSettings(
    session: Session,
    params: {
      monthly_budget: number;
      daily_kwh: number;
      day_rate: number;
      night_rate: number;
      day_start?: number;
      night_start?: number;
      notify_80?: boolean;
      auto_protect?: boolean;
    }
  ): void {
    const user = this.access.require(session, true);
    const monthlyBudget = numberValue(params.monthly_budget, "Месячный лимит расходов", 0.01);
    const dailyKwh = numberValue(params.daily_kwh, "Дневной лимит энергии", 0.01);
    const dayRate = numberValue(params.day_rate, "Дневной тариф", 0);
    const nightRate = numberValue(params.night_rate, "Ночной тариф", 0);
    const dayStart = params.day_start ?? 7;
    const nightStart = params.night_start ?? 23;
    if (
      !Number.isInteger(dayStart) ||
      !Number.isInteger(nightStart) ||
      dayStart < 0 ||
      dayStart >= nightStart ||
      nightStart > 23
    ) {
      throw new ValidationError("Начало дня должно быть раньше начала ночи (часы 0–23).");
    }
    const notify80 = params.notify_80 ?? true;
    const autoProtect = params.auto_protect ?? true;

    const old = this.settings(session, true);
    const s = this.store.settings.get(user.home_id)!;
    s.monthly_budget = monthlyBudget;
    s.daily_kwh = dailyKwh;
    s.notify_80 = Boolean(notify80);
    s.auto_protect = Boolean(autoProtect);

    if (
      old.day_rate !== dayRate ||
      old.night_rate !== nightRate ||
      old.day_start !== dayStart ||
      old.night_start !== nightStart
    ) {
      const tariffId = this.store.nextId("tariff");
      this.store.tariffs.set(tariffId, {
        id: tariffId,
        home_id: user.home_id,
        effective_at_ms: nowMs(),
        day_rate: dayRate,
        night_rate: nightRate,
        day_start: dayStart,
        night_start: nightStart,
      });
    }
    this.store.logEvent(user.home_id, "settings_changed", "Изменены тарифы или лимиты", {
      user_id: user.id,
    });
    this.checkLimits(user.home_id);
  }

  priceSegments(homeId: number, startMs: number, durationSec: number, energyKwh: number): PriceSegment[] {
    const endMs = startMs + durationSec * 1000;
    const tariffs: TariffRow[] = [];
    for (const t of this.store.tariffs.values()) {
      if (t.home_id === homeId && t.effective_at_ms < endMs) {
        tariffs.push(t);
      }
    }
    tariffs.sort((a, b) => a.effective_at_ms - b.effective_at_ms || a.id - b.id);

    const segments: PriceSegment[] = [];
    let cursor = startMs;
    while (cursor < endMs) {
      const applicable = tariffs.filter((t) => t.effective_at_ms <= cursor);
      if (applicable.length === 0) {
        throw new ValidationError("Для даты показания не задан тариф.");
      }
      const tariff = applicable[applicable.length - 1];
      const localHourStart = startOfLocalHourMs(cursor);
      const nextHour = localHourStart + 3_600_000;
      const futureChanges = tariffs.filter((t) => t.effective_at_ms > cursor).map((t) => t.effective_at_ms);
      const boundary = Math.min(endMs, nextHour, ...futureChanges);
      const partEnergy = (energyKwh * (boundary - cursor)) / (durationSec * 1000);
      const localHour = getLocalParts(cursor).hour;
      const rate = localHour >= tariff.day_start && localHour < tariff.night_start ? tariff.day_rate : tariff.night_rate;
      segments.push({
        start_ms: cursor,
        end_ms: boundary,
        energy_kwh: partEnergy,
        cost: partEnergy * rate,
        tariff_id: tariff.id,
      });
      cursor = boundary;
    }
    return segments;
  }

  ingest(session: Session, packet: ReadingPacket): number {
    const device = this.device(session, packet.device_id, true);
    try {
      return this.ingestInternal(device, packet);
    } catch (err) {
      if (err instanceof ValidationError) {
        this.store.logEvent(device.home_id, "invalid_reading", err.message, {
          device_id: device.id,
        });
      }
      throw err;
    }
  }

  private ingestInternal(device: DeviceView, packet: ReadingPacket): number {
    const power = numberValue(packet.power_w, "Мощность", 0, 10_000_000);
    const energy = numberValue(packet.energy_kwh, "Энергия интервала", 0, 1_000_000);
    if (!Number.isInteger(packet.interval_seconds) || packet.interval_seconds < 1 || packet.interval_seconds > 86400) {
      throw new ValidationError("Интервал показания должен быть от 1 секунды до суток.");
    }
    const startMs = packet.measured_at_ms;
    const endMs = startMs + packet.interval_seconds * 1000;
    if (endMs > nowMs() + 60_000) {
      throw new ValidationError("Интервал показания заканчивается в будущем.");
    }
    if (device.state === DeviceState.RETIRED) {
      throw new ValidationError("Нельзя принять показания списанного устройства.");
    }

    const existingReadings = this.store.readingsByDevice.get(device.id) ?? [];
    const previous = existingReadings.length > 0 ? existingReadings[existingReadings.length - 1] : undefined;
    if (previous) {
      const previousEnd = previous.measured_at_ms + previous.interval_seconds * 1000;
      if (startMs < previousEnd) {
        throw new ValidationError("Показание дублируется, пересекается или старше последнего.");
      }
    }

    const segments = this.priceSegments(device.home_id, startMs, packet.interval_seconds, energy);
    const totalCost = segments.reduce((acc, s) => acc + s.cost, 0);
    const readingId = this.store.nextId("reading");
    const readingRow: ReadingRow = {
      id: readingId,
      device_id: device.id,
      measured_at_ms: startMs,
      interval_seconds: packet.interval_seconds,
      power_w: power,
      energy_kwh: energy,
      cost: totalCost,
      tariff_id: segments[0].tariff_id,
    };
    existingReadings.push(readingRow);
    this.store.readingsByDevice.set(device.id, existingReadings);

    let homeSegments = this.store.segmentsByHome.get(device.home_id);
    if (!homeSegments) {
      homeSegments = [];
      this.store.segmentsByHome.set(device.home_id, homeSegments);
    }
    for (const seg of segments) {
      const row: ReadingSegmentRow = {
        id: this.store.nextId("segment"),
        reading_id: readingId,
        device_id: device.id,
        home_id: device.home_id,
        room_id: device.room_id,
        room_name: device.room,
        appliance_name: device.name,
        flexible: device.flexible,
        counted: device.counted,
        start_ms: seg.start_ms,
        end_ms: seg.end_ms,
        energy_kwh: seg.energy_kwh,
        cost: seg.cost,
      };
      homeSegments.push(row);
    }

    this.detectAnomaly(device, packet, readingId);
    this.checkLimits(device.home_id);
    this.store.logEvent(device.home_id, "reading_received", "Показание принято и проверено", {
      device_id: device.id,
    });
    return readingId;
  }

  private detectAnomaly(device: DeviceView, packet: ReadingPacket, readingId: number): void {
    const packetHour = getLocalParts(packet.measured_at_ms).hour;
    const sevenDaysAgo = packet.measured_at_ms - 7 * 86_400_000;
    const readings = this.store.readingsByDevice.get(device.id) ?? [];
    const recent: number[] = [];
    for (let i = readings.length - 1; i >= 0 && recent.length < 24; i--) {
      const r = readings[i];
      if (
        r.id !== readingId &&
        r.measured_at_ms >= sevenDaysAgo &&
        r.measured_at_ms < packet.measured_at_ms &&
        r.power_w > 0 &&
        getLocalParts(r.measured_at_ms).hour === packetHour
      ) {
        recent.push(r.power_w);
      }
    }
    const baseline = recent.length > 0 ? recent.reduce((a, b) => a + b, 0) / recent.length : 0;
    const critical = packet.power_w > device.critical_w;
    const spike = recent.length >= 5 && packet.power_w > baseline * 2 && packet.power_w > device.rated_w * 0.5;
    if (!critical && !spike) return;

    const tariff = this.getActiveTariff(device.home_id, packet.measured_at_ms);
    const night = tariff ? packetHour < tariff.day_start || packetHour >= tariff.night_start : false;
    const kind = critical ? "critical" : night ? "night_spike" : "spike";
    const reference = critical ? device.critical_w : baseline;
    const deviation = (packet.power_w / reference - 1) * 100;
    let message = `${device.name}: ${Math.round(packet.power_w)} Вт, ${
      critical ? "превышен критический порог" : "скачок потребления"
    } на ${Math.round(deviation)}%.`;

    const settings = this.store.settings.get(device.home_id)!;
    if (
      critical &&
      settings.auto_protect &&
      (device.state === DeviceState.IDLE ||
        device.state === DeviceState.RUNNING ||
        device.state === DeviceState.FLEXIBLE)
    ) {
      const nextState = transition(device.state, "anomaly");
      const raw = this.store.devices.get(device.id)!;
      raw.state = nextState;
      message += " Прибор аварийно обесточен в системе.";
      this.store.logEvent(device.home_id, "emergency_shutdown", message, { device_id: device.id });
    }

    this.store.anomalies.push({
      id: this.store.nextId("anomaly"),
      device_id: device.id,
      home_id: device.home_id,
      room_id: device.room_id,
      reading_id: readingId,
      kind,
      deviation,
      message,
      created_at_ms: packet.measured_at_ms,
    });
    this.notifications.send(device.home_id, kind, message, `anomaly:${readingId}`);
  }

  totals(homeId: number, startMs: number, endMs: number, roomId?: number | null, deviceId?: number | null) {
    const segs = this.store.segmentsByHome.get(homeId) ?? [];
    let energy = 0;
    let cost = 0;
    for (const s of segs) {
      if (!s.counted) continue;
      if (s.start_ms >= endMs || s.end_ms <= startMs) continue;
      if (roomId !== undefined && roomId !== null && s.room_id !== roomId) continue;
      if (deviceId !== undefined && deviceId !== null && s.device_id !== deviceId) continue;
      const overlap = Math.max(0, Math.min(endMs, s.end_ms) - Math.max(startMs, s.start_ms));
      const fraction = overlap / (s.end_ms - s.start_ms);
      energy += s.energy_kwh * fraction;
      cost += s.cost * fraction;
    }
    return { energy, cost };
  }

  checkLimits(homeId: number): void {
    const settings = this.store.settings.get(homeId);
    if (!settings) return;
    const current = nowMs();
    const todayStart = startOfLocalDayMs(current);
    const p = getLocalParts(current);
    const monthStart = startOfLocalDayMs(todayStart - (p.day - 1) * 86_400_000);
    // Ensure exact 1st of month in Europe/Minsk
    const monthParts = getLocalParts(current);
    const monthKey = `${monthParts.year}-${String(monthParts.month).padStart(2, "0")}`;
    const dayKey = `${monthParts.year}-${String(monthParts.month).padStart(2, "0")}-${String(monthParts.day).padStart(2, "0")}`;

    const monthTotals = this.totals(homeId, monthStart, current);
    const dayTotals = this.totals(homeId, todayStart, current);
    const budget = settings.monthly_budget;

    if (monthTotals.cost >= budget) {
      this.notifications.send(
        homeId,
        "budget",
        `Месячный лимит расходов превышен: ${monthTotals.cost.toFixed(2)} / ${budget.toFixed(2)} BYN.`,
        `budget:100:${monthKey}`
      );
    } else if (settings.notify_80 && monthTotals.cost >= budget * 0.8) {
      this.notifications.send(
        homeId,
        "budget",
        `Достигнуто 80% месячного лимита: ${monthTotals.cost.toFixed(2)} / ${budget.toFixed(2)} BYN.`,
        `budget:80:${monthKey}`
      );
    }

    if (dayTotals.energy >= settings.daily_kwh) {
      this.notifications.send(
        homeId,
        "daily",
        `Превышен дневной лимит: ${dayTotals.energy.toFixed(2)} / ${settings.daily_kwh.toFixed(2)} кВт·ч.`,
        `daily:${dayKey}`
      );
    }
  }

  events(session: Session) {
    const user = this.access.require(session, true);
    const rows = [];
    for (let i = this.store.events.length - 1; i >= 0 && rows.length < 300; i--) {
      const e = this.store.events[i];
      if (e.home_id === user.home_id) rows.push(e);
    }
    return rows;
  }
}
