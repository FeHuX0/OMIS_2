import {
  DeviceState,
  DomainError,
  ReadingPacket,
  Session,
  ValidationError,
  getLocalParts,
  nowMs,
  parseIsoWithTz,
} from "./domain.js";
import { EnergyService } from "./services.js";

export class SensorGateway {
  private pending: Array<{ session: Session; packet: ReadingPacket }> = [];

  constructor(private readonly service: EnergyService) {}

  submit(session: Session, packet: ReadingPacket): void {
    this.pending.push({ session, packet });
  }

  drain(maximum = 100): { count: number; errors: string[] } {
    const errors: string[] = [];
    let count = 0;
    while (this.pending.length > 0 && count < maximum) {
      const item = this.pending.shift()!;
      try {
        this.service.ingest(item.session, item.packet);
      } catch (err) {
        if (err instanceof DomainError) {
          errors.push(err.message);
        } else {
          throw err;
        }
      }
      count += 1;
    }
    return { count, errors };
  }
}

export function importCsvContent(
  service: EnergyService,
  session: Session,
  rawContent: string
): { accepted: number; errors: string[] } {
  const user = service.access.require(session, true);
  const clean = rawContent.replace(/^\uFEFF/, "");
  const lines = clean.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) {
    throw new ValidationError("В CSV нужны столбцы: energy_kwh, interval_seconds, measured_at, power_w, serial");
  }
  const delimiter = lines[0].includes(";") ? ";" : ",";
  const headers = lines[0].split(delimiter).map((h) => h.trim().replace(/^"|"$/g, ""));
  const required = ["serial", "measured_at", "power_w", "energy_kwh", "interval_seconds"];
  for (const col of required) {
    if (!headers.includes(col)) {
      throw new ValidationError("В CSV нужны столбцы: " + required.sort().join(", "));
    }
  }
  const colIndex = new Map<string, number>();
  headers.forEach((h, i) => colIndex.set(h, i));

  const devicesBySerial = new Map(
    service.devices(session).map((d) => [d.serial.toLowerCase(), d])
  );

  let accepted = 0;
  const errors: string[] = [];

  for (let i = 1; i < lines.length; i++) {
    const lineNum = i + 1;
    const cols = lines[i].split(delimiter).map((c) => c.trim().replace(/^"|"$/g, ""));
    try {
      const serialRaw = cols[colIndex.get("serial")!] ?? "";
      const device = devicesBySerial.get(serialRaw.trim().toLowerCase());
      if (!device) {
        throw new ValidationError("Серийный номер не зарегистрирован в вашем доме.");
      }
      const measuredAtStr = cols[colIndex.get("measured_at")!] ?? "";
      const measuredAtMs = parseIsoWithTz(measuredAtStr);
      const powerW = Number(cols[colIndex.get("power_w")!]);
      const energyKwh = Number(cols[colIndex.get("energy_kwh")!]);
      const intervalStr = cols[colIndex.get("interval_seconds")!] ?? "";
      const intervalSeconds = Number(intervalStr);
      if (!Number.isFinite(powerW) || !Number.isFinite(energyKwh) || !Number.isInteger(intervalSeconds)) {
        throw new ValidationError("Некорректные числовые значения в строке CSV.");
      }
      service.ingest(session, {
        device_id: device.id,
        measured_at_ms: measuredAtMs,
        power_w: powerW,
        energy_kwh: energyKwh,
        interval_seconds: intervalSeconds,
      });
      accepted += 1;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const formatted = `Строка ${lineNum}: ${msg}`;
      errors.push(formatted);
      service.store.logEvent(user.home_id, "import_error", formatted);
    }
  }

  return { accepted, errors };
}

export class Simulator {
  constructor(
    private readonly service: EnergyService,
    private readonly gateway: SensorGateway
  ) {}

  tick(session: Session): void {
    const currentMs = nowMs();
    const localHour = getLocalParts(currentMs).hour;
    for (const device of this.service.devices(session)) {
      const last = this.service.lastReading(device.id);
      let startMs = currentMs - 10_000;
      if (last) {
        const lastEndMs = last.measured_at_ms + last.interval_seconds * 1000;
        startMs = Math.max(startMs, lastEndMs);
      }
      const durationSec = Math.floor((currentMs - startMs) / 1000);
      if (durationSec < 1) continue;

      let power = 0;
      if (device.state === DeviceState.IDLE || device.state === DeviceState.RUNNING) {
        let factor = (localHour >= 7 && localHour < 23) || device.essential ? 0.25 : 0.015;
        if (device.flexible) {
          factor = localHour === 19 || localHour === 20 ? 0.65 : 0.005;
        }
        if (device.state === DeviceState.IDLE && !device.essential) {
          factor = 0.005;
        }
        const jitter = 0.8 + Math.random() * 0.4;
        power = device.rated_w * factor * jitter;
      }

      this.gateway.submit(session, {
        device_id: device.id,
        measured_at_ms: startMs,
        power_w: power,
        energy_kwh: (power / 1000) * (durationSec / 3600),
        interval_seconds: durationSec,
      });
    }
  }
}
