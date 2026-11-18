export const LOCAL_TIMEZONE = "Europe/Minsk";
// Europe/Minsk has been permanently UTC+3 (+03:00) since 2011, and we also use Intl for local calendar fields.

export interface LocalParts {
  year: number;
  month: number; // 1-12
  day: number; // 1-31
  hour: number; // 0-23
  minute: number; // 0-59
  second: number; // 0-59
  weekday: number; // 0=Mon .. 6=Sun
}

const partsFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: LOCAL_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  weekday: "short",
});

const WEEKDAY_MAP: Record<string, number> = {
  Mon: 0,
  Tue: 1,
  Wed: 2,
  Thu: 3,
  Fri: 4,
  Sat: 5,
  Sun: 6,
};

export function nowMs(): number {
  return Date.now();
}

export function getLocalParts(ms: number): LocalParts {
  const parts = partsFormatter.formatToParts(new Date(ms));
  const map: Record<string, string> = {};
  for (const p of parts) {
    if (p.type !== "literal") map[p.type] = p.value;
  }
  return {
    year: Number(map.year),
    month: Number(map.month),
    day: Number(map.day),
    hour: Number(map.hour) % 24,
    minute: Number(map.minute),
    second: Number(map.second),
    weekday: WEEKDAY_MAP[map.weekday] ?? 0,
  };
}

// Convert Europe/Minsk local calendar date/time to UTC epoch ms.
// Uses iterative offset probe via Intl so it stays exact for any timezone rule.
export function localToUtcMs(
  year: number,
  month: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
  ms = 0
): number {
  // Initial guess: Europe/Minsk is UTC+3
  let guess = Date.UTC(year, month - 1, day, hour - 3, minute, second, ms);
  for (let i = 0; i < 2; i++) {
    const p = getLocalParts(guess);
    const actualAsUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second, 0);
    const targetAsUtc = Date.UTC(year, month - 1, day, hour, minute, second, 0);
    const diff = targetAsUtc - actualAsUtc;
    if (diff === 0) break;
    guess += diff;
  }
  return guess;
}

export function startOfLocalDayMs(ms: number): number {
  const p = getLocalParts(ms);
  return localToUtcMs(p.year, p.month, p.day, 0, 0, 0, 0);
}

export function startOfLocalHourMs(ms: number): number {
  const p = getLocalParts(ms);
  return localToUtcMs(p.year, p.month, p.day, p.hour, 0, 0, 0);
}

export function addLocalDaysMs(ms: number, days: number): number {
  const p = getLocalParts(ms);
  const d = new Date(Date.UTC(p.year, p.month - 1, p.day + days));
  return localToUtcMs(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(), p.hour, p.minute, p.second, 0);
}

export function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function formatLocalDateTime(ms: number): string {
  const p = getLocalParts(ms);
  return `${pad2(p.day)}.${pad2(p.month)}.${p.year} ${pad2(p.hour)}:${pad2(p.minute)}:${pad2(p.second)}`;
}

export function formatLocalShortDateTime(ms: number): string {
  const p = getLocalParts(ms);
  return `${pad2(p.day)}.${pad2(p.month)} ${pad2(p.hour)}:${pad2(p.minute)}`;
}

export function formatLocalShortDateTimeSec(ms: number): string {
  const p = getLocalParts(ms);
  return `${pad2(p.day)}.${pad2(p.month)} ${pad2(p.hour)}:${pad2(p.minute)}:${pad2(p.second)}`;
}

export function formatLocalReportRange(ms: number): string {
  const p = getLocalParts(ms);
  return `${pad2(p.day)}.${pad2(p.month)}.${p.year} ${pad2(p.hour)}:${pad2(p.minute)}`;
}

export function formatLocalDate(ms: number): string {
  const p = getLocalParts(ms);
  return `${pad2(p.day)}.${pad2(p.month)}.${p.year}`;
}

export function formatLocalTime(ms: number): string {
  const p = getLocalParts(ms);
  return `${pad2(p.hour)}:${pad2(p.minute)}:${pad2(p.second)}`;
}

export function formatBucketLabel(ms: number, period: string): string {
  const p = getLocalParts(ms);
  if (period === "day") {
    return `${pad2(p.hour)}:${pad2(p.minute)}`;
  }
  return `${pad2(p.day)}.${pad2(p.month)}`;
}

export function parseIsoWithTz(raw: string): number {
  const trimmed = (raw ?? "").trim();
  // Require explicit timezone offset (Z or +HH:MM / -HH:MM / +HHMM)
  const hasTz = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed);
  if (!hasTz) {
    throw new ValidationError("Укажите часовой пояс времени показания.");
  }
  const ms = Date.parse(trimmed);
  if (!Number.isFinite(ms)) {
    throw new ValidationError("Некорректный формат даты показания.");
  }
  return ms;
}

export class DomainError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class ValidationError extends DomainError {}

export class AccessDenied extends DomainError {}

export enum Role {
  OWNER = "owner",
  FAMILY = "family",
}

export enum DeviceState {
  OFF = "off",
  IDLE = "idle",
  RUNNING = "running",
  FLEXIBLE = "flexible",
  EMERGENCY = "emergency",
  RETIRED = "retired",
}

export type DeviceKind = "meter" | "socket" | "sensor";

export const STATE_LABELS: Record<DeviceState, string> = {
  [DeviceState.OFF]: "Выключен",
  [DeviceState.IDLE]: "Ожидание активности",
  [DeviceState.RUNNING]: "Штатный режим",
  [DeviceState.FLEXIBLE]: "Ожидание ночного тарифа",
  [DeviceState.EMERGENCY]: "Аварийное обесточивание",
  [DeviceState.RETIRED]: "Списан",
};

export const ACTION_LABELS: Record<string, string> = {
  enable: "Включить питание",
  start: "Начать работу",
  finish: "Завершить цикл",
  disable: "Выключить",
  schedule: "Перенести на ночь",
  reset: "Авария устранена: сбросить",
  retire: "Списать устройство",
};

export const KIND_LABELS: Record<DeviceKind, string> = {
  meter: "Счётчик",
  socket: "Умная розетка",
  sensor: "Датчик",
};

export const TRANSITIONS: Record<DeviceState, Record<string, DeviceState>> = {
  [DeviceState.OFF]: { enable: DeviceState.IDLE, retire: DeviceState.RETIRED },
  [DeviceState.IDLE]: {
    start: DeviceState.RUNNING,
    disable: DeviceState.OFF,
    schedule: DeviceState.FLEXIBLE,
    anomaly: DeviceState.EMERGENCY,
    retire: DeviceState.RETIRED,
  },
  [DeviceState.RUNNING]: {
    finish: DeviceState.IDLE,
    disable: DeviceState.OFF,
    schedule: DeviceState.FLEXIBLE,
    anomaly: DeviceState.EMERGENCY,
    retire: DeviceState.RETIRED,
  },
  [DeviceState.FLEXIBLE]: {
    night: DeviceState.RUNNING,
    disable: DeviceState.OFF,
    anomaly: DeviceState.EMERGENCY,
    retire: DeviceState.RETIRED,
  },
  [DeviceState.EMERGENCY]: { reset: DeviceState.OFF },
  [DeviceState.RETIRED]: {},
};

export function transition(state: DeviceState, action: string): DeviceState {
  const result = TRANSITIONS[state]?.[action];
  if (!result) {
    throw new ValidationError(`Действие «${action}» недоступно: ${STATE_LABELS[state]}.`);
  }
  return result;
}

export interface Session {
  token: string;
}

export interface ReadingPacket {
  device_id: number;
  measured_at_ms: number;
  power_w: number;
  energy_kwh: number;
  interval_seconds: number;
}

export interface ReportDeviceRow {
  name: string;
  room: string;
  energy_kwh: number;
  cost: number;
}

export interface AnomalyView {
  id: number;
  device_id: number;
  reading_id: number;
  kind: string;
  deviation: number;
  message: string;
  created_at_ms: number;
  name: string;
  room: string;
}

export interface Report {
  start_ms: number;
  end_ms: number;
  energy_kwh: number;
  cost: number;
  previous_kwh: number;
  change_percent: number | null;
  series: Array<[string, number]>;
  devices: ReportDeviceRow[];
  anomalies: AnomalyView[];
  recommendations: string[];
}
