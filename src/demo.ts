import { DeviceKind, getLocalParts, nowMs, startOfLocalHourMs } from "./domain.js";
import { EnergyService } from "./services.js";
import { ReadingRow, ReadingSegmentRow } from "./store.js";

export const DEMO_PASSWORD = "Energy2026!";

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function next(): number {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function seedDemo(energy: EnergyService): void {
  if (energy.store.usersByLogin.has("owner@demo.local")) {
    return;
  }
  const access = energy.access;
  const session = access.registerHome(
    "Алексей",
    "owner@demo.local",
    DEMO_PASSWORD,
    "Мой дом · демонстрация",
    "Минск",
    "apartment"
  );
  access.addFamily(session, "Член семьи", "family@demo.local", DEMO_PASSWORD);

  const kitchen = energy.addRoom(session, "Кухня");
  const living = energy.addRoom(session, "Гостиная");
  const bathroom = energy.addRoom(session, "Ванная");
  const bedroom = energy.addRoom(session, "Спальня");

  const definitions: Array<[number, string, string, DeviceKind, number, number, boolean, boolean]> = [
    [kitchen, "Холодильник", "DEMO-FRIDGE", "socket", 180, 900, false, true],
    [kitchen, "Посудомоечная машина", "DEMO-DISH", "socket", 1600, 3500, true, false],
    [living, "Кондиционер", "DEMO-AC", "sensor", 1200, 3500, false, false],
    [living, "Телевизор", "DEMO-TV", "socket", 160, 600, false, false],
    [bathroom, "Стиральная машина", "DEMO-WASH", "socket", 2000, 4000, true, false],
    [bedroom, "Освещение", "DEMO-LIGHT", "sensor", 120, 500, false, false],
  ];

  const devices = definitions.map((def) => {
    const devId = energy.addDevice(session, ...def);
    energy.changeState(session, devId, "start");
    return energy.device(session, devId);
  });

  const user = access.require(session);
  const rng = mulberry32(42);
  const endMs = startOfLocalHourMs(nowMs());
  const startMs = endMs - 65 * 86_400_000;
  // Align startMs to local midnight 65 days ago
  const pEnd = getLocalParts(endMs);
  const startMidnightMs = endMs - (pEnd.hour * 3_600_000) - 65 * 86_400_000;
  const tariff = energy.getActiveTariff(user.home_id, endMs)!;

  let homeSegments = energy.store.segmentsByHome.get(user.home_id);
  if (!homeSegments) {
    homeSegments = [];
    energy.store.segmentsByHome.set(user.home_id, homeSegments);
  }

  for (let cursorMs = startMidnightMs; cursorMs < endMs; cursorMs += 3_600_000) {
    const hour = getLocalParts(cursorMs).hour;
    const daysElapsed = Math.floor((cursorMs - startMidnightMs) / 86_400_000);
    for (const d of devices) {
      const active = hour >= 7 && hour < 23;
      let factor = d.essential ? 0.25 : active ? 0.25 : 0.015;
      if (d.flexible) {
        factor = hour === 19 || hour === 20 ? 0.65 : 0.005;
      }
      const trend = 1 + 0.09 * Math.sin(daysElapsed / 8);
      const power = d.rated_w * factor * (0.8 + rng() * 0.4) * trend;
      const energyKwh = power / 1000;
      const cost = energyKwh * (active ? 0.28 : 0.14);

      const readingId = energy.store.nextId("reading");
      const rRow: ReadingRow = {
        id: readingId,
        device_id: d.id,
        measured_at_ms: cursorMs,
        interval_seconds: 3600,
        power_w: power,
        energy_kwh: energyKwh,
        cost,
        tariff_id: tariff.id,
      };
      let list = energy.store.readingsByDevice.get(d.id);
      if (!list) {
        list = [];
        energy.store.readingsByDevice.set(d.id, list);
      }
      list.push(rRow);

      const segRow: ReadingSegmentRow = {
        id: energy.store.nextId("segment"),
        reading_id: readingId,
        device_id: d.id,
        home_id: user.home_id,
        room_id: d.room_id,
        room_name: d.room,
        appliance_name: d.name,
        flexible: d.flexible,
        counted: d.counted,
        start_ms: cursorMs,
        end_ms: cursorMs + 3_600_000,
        energy_kwh: energyKwh,
        cost,
      };
      homeSegments.push(segRow);
    }
  }

  energy.store.logEvent(user.home_id, "demo_seeded", "Загружены 65 дней демонстрационных данных");
  energy.checkLimits(user.home_id);
  access.logout(session);
}
