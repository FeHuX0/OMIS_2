import { DeviceKind, DeviceState, Role, nowMs } from "./domain.js";

export interface HomeRow {
  id: number;
  name: string;
  address: string;
  kind: "apartment" | "house";
  timezone: string;
}

export interface UserRow {
  id: number;
  home_id: number;
  name: string;
  login: string;
  password: string;
  role: Role;
  active: boolean;
  created_at_ms: number;
}

export interface RoomRow {
  id: number;
  home_id: number;
  name: string;
}

export interface ApplianceRow {
  id: number;
  room_id: number;
  name: string;
  rated_w: number;
  flexible: boolean;
  essential: boolean;
}

export interface DeviceRow {
  id: number;
  appliance_id: number;
  serial: string;
  kind: DeviceKind;
  state: DeviceState;
  critical_w: number;
  counted: boolean;
  created_at_ms: number;
}

export interface TariffRow {
  id: number;
  home_id: number;
  effective_at_ms: number;
  day_rate: number;
  night_rate: number;
  day_start: number;
  night_start: number;
}

export interface SettingsRow {
  home_id: number;
  monthly_budget: number;
  daily_kwh: number;
  notify_80: boolean;
  auto_protect: boolean;
  scenario: string;
}

export interface ReadingRow {
  id: number;
  device_id: number;
  measured_at_ms: number;
  interval_seconds: number;
  power_w: number;
  energy_kwh: number;
  cost: number;
  tariff_id: number;
}

export interface ReadingSegmentRow {
  id: number;
  reading_id: number;
  device_id: number;
  home_id: number;
  room_id: number;
  room_name: string;
  appliance_name: string;
  flexible: boolean;
  counted: boolean;
  start_ms: number;
  end_ms: number;
  energy_kwh: number;
  cost: number;
}

export interface AnomalyRow {
  id: number;
  device_id: number;
  home_id: number;
  room_id: number;
  reading_id: number;
  kind: string;
  deviation: number;
  message: string;
  created_at_ms: number;
}

export interface NotificationRow {
  id: number;
  home_id: number;
  kind: string;
  message: string;
  created_at_ms: number;
  dedupe_key: string | null;
}

export interface EventRow {
  id: number;
  home_id: number | null;
  user_id: number | null;
  device_id: number | null;
  kind: string;
  message: string;
  created_at_ms: number;
}

export interface ReportExportRow {
  id: number;
  home_id: number;
  user_id: number;
  start_ms: number;
  end_ms: number;
  format: string;
  created_at_ms: number;
}

export interface BuildingRow {
  id: number;
  home_id: number;
  name: string;
}

export interface BuildingApartmentRow {
  id: number;
  building_id: number;
  name: string;
  area: number;
  residents: number;
  own_kwh: number;
}

export class MemoryStore {
  private seq = {
    home: 0,
    user: 0,
    room: 0,
    appliance: 0,
    device: 0,
    tariff: 0,
    reading: 0,
    segment: 0,
    anomaly: 0,
    notification: 0,
    event: 0,
    report: 0,
    building: 0,
    apartment: 0,
  };

  homes = new Map<number, HomeRow>();
  users = new Map<number, UserRow>();
  usersByLogin = new Map<string, UserRow>();
  rooms = new Map<number, RoomRow>();
  appliances = new Map<number, ApplianceRow>();
  devices = new Map<number, DeviceRow>();
  devicesBySerial = new Map<string, DeviceRow>();
  tariffs = new Map<number, TariffRow>();
  settings = new Map<number, SettingsRow>();

  // Indexed by device_id in ascending chronological order of measured_at_ms
  readingsByDevice = new Map<number, ReadingRow[]>();
  // Indexed by home_id for fast dashboard/report aggregation
  segmentsByHome = new Map<number, ReadingSegmentRow[]>();

  anomalies: AnomalyRow[] = [];
  notifications: NotificationRow[] = [];
  notificationDedupeKeys = new Set<string>();
  notificationReads = new Set<string>();
  events: EventRow[] = [];
  reports: ReportExportRow[] = [];
  buildingsByHome = new Map<number, BuildingRow>();
  apartments: BuildingApartmentRow[] = [];

  nextId(key: keyof MemoryStore["seq"]): number {
    this.seq[key] += 1;
    return this.seq[key];
  }

  logEvent(
    home_id: number | null,
    kind: string,
    message: string,
    opts?: { user_id?: number | null; device_id?: number | null }
  ): void {
    this.events.push({
      id: this.nextId("event"),
      home_id,
      user_id: opts?.user_id ?? null,
      device_id: opts?.device_id ?? null,
      kind,
      message,
      created_at_ms: nowMs(),
    });
  }
}
