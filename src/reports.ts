import {
  AnomalyView,
  Report,
  ReportDeviceRow,
  Session,
  ValidationError,
  addLocalDaysMs,
  formatBucketLabel,
  formatLocalDate,
  formatLocalReportRange,
  getLocalParts,
  localToUtcMs,
  nowMs,
  startOfLocalDayMs,
  startOfLocalHourMs,
} from "./domain.js";
import { EnergyService } from "./services.js";
import { UserRow } from "./store.js";

export function periodBounds(period: string, anchorMs: number): { startMs: number; fullEndMs: number; previousStartMs: number } {
  const p = getLocalParts(anchorMs);
  const dayStart = localToUtcMs(p.year, p.month, p.day, 0, 0, 0, 0);
  if (period === "day") {
    return {
      startMs: dayStart,
      fullEndMs: addLocalDaysMs(dayStart, 1),
      previousStartMs: addLocalDaysMs(dayStart, -1),
    };
  }
  if (period === "week") {
    const startMs = addLocalDaysMs(dayStart, -p.weekday);
    return {
      startMs,
      fullEndMs: addLocalDaysMs(startMs, 7),
      previousStartMs: addLocalDaysMs(startMs, -7),
    };
  }
  if (period === "month") {
    const startMs = localToUtcMs(p.year, p.month, 1, 0, 0, 0, 0);
    const nextMonthYear = p.month === 12 ? p.year + 1 : p.year;
    const nextMonth = p.month === 12 ? 1 : p.month + 1;
    const fullEndMs = localToUtcMs(nextMonthYear, nextMonth, 1, 0, 0, 0, 0);
    const prevYear = p.month === 1 ? p.year - 1 : p.year;
    const prevMonth = p.month === 1 ? 12 : p.month - 1;
    const previousStartMs = localToUtcMs(prevYear, prevMonth, 1, 0, 0, 0, 0);
    return { startMs, fullEndMs, previousStartMs };
  }
  throw new ValidationError("Выберите день, неделю или месяц.");
}

function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeCsvCell(value: string): string {
  const trimmed = value ?? "";
  const guarded = ["=", "+", "-", "@"].includes(trimmed.slice(0, 1)) ? `'${trimmed}` : trimmed;
  if (guarded.includes(";") || guarded.includes('"') || guarded.includes("\n")) {
    return `"${guarded.replace(/"/g, '""')}"`;
  }
  return guarded;
}

function encodeUtf16BeHex(str: string): string {
  const bytes: number[] = [];
  for (let i = 0; i < str.length; i++) {
    const code = str.charCodeAt(i);
    bytes.push((code >> 8) & 0xff, code & 0xff);
  }
  return Buffer.from(bytes).toString("hex").toUpperCase();
}

export class ReportService {
  constructor(private readonly energy: EnergyService) {}

  build(
    session: Session,
    period = "month",
    anchorMs?: number | null,
    roomId?: number | null,
    deviceId?: number | null
  ): Report {
    const user = this.energy.access.require(session);
    const currentMs = nowMs();
    const { startMs, fullEndMs, previousStartMs } = periodBounds(period, anchorMs ?? currentMs);
    const endMs = Math.min(fullEndMs, currentMs);
    if (endMs <= startMs) {
      throw new ValidationError("Период ещё не начался.");
    }
    const previousEndMs =
      endMs === fullEndMs ? startMs : Math.min(startMs, previousStartMs + (endMs - startMs));
    const prevTotals = this.energy.totals(user.home_id, previousStartMs, previousEndMs, roomId, deviceId);

    const buckets = new Map<number, number>();
    let cursor = startMs;
    while (cursor < endMs) {
      buckets.set(cursor, 0);
      cursor = period === "day" ? cursor + 3_600_000 : addLocalDaysMs(cursor, 1);
    }

    const segs = this.energy.store.segmentsByHome.get(user.home_id) ?? [];
    let energy = 0;
    let cost = 0;
    let flexibleEnergy = 0;
    const byDevice = new Map<number, ReportDeviceRow>();
    let matchedCount = 0;

    for (const s of segs) {
      if (!s.counted) continue;
      if (s.start_ms >= endMs || s.end_ms <= startMs) continue;
      if (roomId !== undefined && roomId !== null && s.room_id !== roomId) continue;
      if (deviceId !== undefined && deviceId !== null && s.device_id !== deviceId) continue;

      matchedCount += 1;
      const overlap = Math.max(0, Math.min(endMs, s.end_ms) - Math.max(startMs, s.start_ms));
      const fraction = overlap / (s.end_ms - s.start_ms);
      const e = s.energy_kwh * fraction;
      const c = s.cost * fraction;
      energy += e;
      cost += c;
      if (s.flexible) flexibleEnergy += e;

      let devEntry = byDevice.get(s.device_id);
      if (!devEntry) {
        devEntry = { name: s.appliance_name, room: s.room_name, energy_kwh: 0, cost: 0 };
        byDevice.set(s.device_id, devEntry);
      }
      devEntry.energy_kwh += e;
      devEntry.cost += c;

      const leftMs = Math.max(startMs, s.start_ms);
      const bucketKey = period === "day" ? startOfLocalHourMs(leftMs) : startOfLocalDayMs(leftMs);
      if (buckets.has(bucketKey)) {
        buckets.set(bucketKey, (buckets.get(bucketKey) ?? 0) + e);
      }
    }

    const top = Array.from(byDevice.values()).sort((a, b) => b.energy_kwh - a.energy_kwh);
    const anomalies: AnomalyView[] = [];
    for (const a of this.energy.store.anomalies) {
      if (a.home_id !== user.home_id) continue;
      if (a.created_at_ms < startMs || a.created_at_ms >= endMs) continue;
      if (roomId !== undefined && roomId !== null && a.room_id !== roomId) continue;
      if (deviceId !== undefined && deviceId !== null && a.device_id !== deviceId) continue;
      const dev = this.energy.store.devices.get(a.device_id);
      const app = dev ? this.energy.store.appliances.get(dev.appliance_id) : undefined;
      const room = app ? this.energy.store.rooms.get(app.room_id) : undefined;
      anomalies.push({
        ...a,
        name: app?.name ?? "",
        room: room?.name ?? "",
      });
    }
    anomalies.sort((a, b) => b.created_at_ms - a.created_at_ms);

    const recommendations: string[] = [];
    if (top.length > 0 && energy > 0) {
      recommendations.push(
        `Наибольший вклад: ${top[0].name} — ${Math.round((top[0].energy_kwh / energy) * 100)}% расхода. Проверьте время работы и настройки прибора.`
      );
    }
    if (flexibleEnergy > 0) {
      recommendations.push(
        `Гибкая нагрузка: ${flexibleEnergy.toFixed(2)} кВт·ч. Перенесите стирку или зарядку на часы низкого тарифа.`
      );
    }
    if (anomalies.length > 0) {
      recommendations.push("Выявлены аномалии. Проверьте приборы до сброса аварии.");
    }
    const change = prevTotals.energy > 0 ? (energy / prevTotals.energy - 1) * 100 : null;
    if (change !== null && change > 15) {
      recommendations.push(`Расход вырос на ${change.toFixed(1)}%. Сравните режимы работы приборов.`);
    }
    if (matchedCount === 0) {
      recommendations.push("За выбранный период нет показаний. Проверьте подключение устройств.");
    }

    const series: Array<[string, number]> = Array.from(buckets.entries()).map(([t, v]) => [
      formatBucketLabel(t, period),
      Number(v.toFixed(5)),
    ]);

    return {
      start_ms: startMs,
      end_ms: endMs,
      energy_kwh: energy,
      cost,
      previous_kwh: prevTotals.energy,
      change_percent: change,
      series,
      devices: top,
      anomalies,
      recommendations,
    };
  }

  dashboard(session: Session, roomId?: number | null, deviceId?: number | null) {
    const user = this.energy.access.require(session);
    const currentMs = nowMs();
    const todayStart = startOfLocalDayMs(currentMs);
    const p = getLocalParts(currentMs);
    const monthStart = localToUtcMs(p.year, p.month, 1, 0, 0, 0, 0);
    const daily = this.energy.totals(user.home_id, todayStart, currentMs, roomId, deviceId);
    const monthly = this.energy.totals(user.home_id, monthStart, currentMs, roomId, deviceId);
    return {
      daily_kwh: daily.energy,
      daily_cost: daily.cost,
      monthly_kwh: monthly.energy,
      monthly_cost: monthly.cost,
    };
  }

  csvBytes(session: Session, report: Report): Buffer {
    const user = this.energy.access.require(session);
    const lines: string[] = [];
    lines.push(["Тип", "Период / прибор", "Комната", "Энергия, кВт·ч", "Стоимость, BYN"].map(safeCsvCell).join(";"));
    lines.push(
      [
        "Итого",
        `${formatLocalDate(report.start_ms)} — ${formatLocalDate(report.end_ms)}`,
        "",
        report.energy_kwh.toFixed(5),
        report.cost.toFixed(5),
      ]
        .map(safeCsvCell)
        .join(";")
    );
    for (const [label, energy] of report.series) {
      lines.push(["График", label, "", energy.toFixed(5), ""].map(safeCsvCell).join(";"));
    }
    for (const device of report.devices) {
      lines.push(
        ["Прибор", device.name, device.room, device.energy_kwh.toFixed(5), device.cost.toFixed(5)]
          .map(safeCsvCell)
          .join(";")
      );
    }
    this.recordExport(user, report, "csv");
    return Buffer.from("\uFEFF" + lines.join("\r\n") + "\r\n", "utf-8");
  }

  html(session: Session, report: Report): string {
    this.energy.access.require(session);
    const rows = report.devices
      .map(
        (d) =>
          `<tr><td>${escapeHtml(d.name)}</td><td>${escapeHtml(d.room)}</td><td>${d.energy_kwh.toFixed(
            3
          )}</td><td>${d.cost.toFixed(2)}</td></tr>`
      )
      .join("");
    const recs = report.recommendations.map((r) => `<li>${escapeHtml(r)}</li>`).join("");
    const history = report.series
      .map(([label, val]) => `<tr><td>${escapeHtml(label)}</td><td>${val.toFixed(3)}</td></tr>`)
      .join("");
    const anomalies = report.anomalies.map((a) => `<li>${escapeHtml(a.message)}</li>`).join("");
    const change =
      report.change_percent !== null
        ? `${report.change_percent >= 0 ? "+" : ""}${report.change_percent.toFixed(1)}%`
        : "недостаточно данных для сравнения";
    return (
      "<html><head><meta charset='utf-8'></head><body style='font-family:sans-serif'>" +
      `<h1>Energy Home — отчёт</h1><p>${formatLocalReportRange(report.start_ms)} — ${formatLocalReportRange(
        report.end_ms
      )} (Минск)</p><h2>${report.energy_kwh.toFixed(3)} кВт·ч · ${report.cost.toFixed(2)} BYN</h2>` +
      `<p>Предыдущий сопоставимый период: ${report.previous_kwh.toFixed(3)} кВт·ч. Изменение: ${change}.</p>` +
      `<table border='1' cellpadding='8'><tr><th>Прибор</th><th>Комната</th><th>кВт·ч</th><th>BYN</th></tr>${rows}</table>` +
      `<h2>Динамика расхода</h2><table border='1' cellpadding='5'><tr><th>Период</th><th>кВт·ч</th></tr>${history}</table>` +
      `<h2>Аномалии</h2><ul>${anomalies || "<li>Не обнаружены</li>"}</ul>` +
      `<h2>Рекомендации</h2><ul>${recs}</ul></body></html>`
    );
  }

  pdfBytes(session: Session, report: Report): Buffer {
    const user = this.energy.access.require(session);
    const lines: string[] = [
      "Energy Home — Отчёт по энергопотреблению",
      `Период: ${formatLocalReportRange(report.start_ms)} — ${formatLocalReportRange(report.end_ms)} (Минск)`,
      `Итого: ${report.energy_kwh.toFixed(3)} кВт·ч | Стоимость: ${report.cost.toFixed(2)} BYN`,
      `Предыдущий период: ${report.previous_kwh.toFixed(3)} кВт·ч`,
      "",
      "Вклад приборов:",
      ...report.devices.map(
        (d) => `  - ${d.name} (${d.room}): ${d.energy_kwh.toFixed(3)} кВт·ч, ${d.cost.toFixed(2)} BYN`
      ),
      "",
      "Динамика по интервалам:",
      ...report.series.slice(0, 31).map(([lbl, val]) => `  ${lbl}: ${val.toFixed(3)} кВт·ч`),
      "",
      "Аномалии:",
      ...(report.anomalies.length > 0
        ? report.anomalies.slice(0, 10).map((a) => `  ! ${a.message}`)
        : ["  Не обнаружены"]),
      "",
      "Рекомендации:",
      ...report.recommendations.map((r) => `  * ${r}`),
    ];

    const streamOps = ["BT", "/F1 11 Tf", "50 790 Td", "15 TL"];
    for (const line of lines.slice(0, 46)) {
      const hasNonAscii = /[^\x00-\x7f]/.test(line);
      if (hasNonAscii) {
        streamOps.push(`<${encodeUtf16BeHex(line)}> Tj T*`);
      } else {
        const asciiSafe = line.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
        streamOps.push(`(${asciiSafe}) Tj T*`);
      }
    }
    streamOps.push("ET");
    const content = Buffer.from(streamOps.join("\n"), "latin1");
    const htmlComment = Buffer.from(this.html(session, report), "utf-8");

    const objects: Buffer[] = [
      Buffer.from("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n", "ascii"),
      Buffer.from("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n", "ascii"),
      Buffer.from(
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        "ascii"
      ),
      Buffer.from("4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n", "ascii"),
      Buffer.concat([
        Buffer.from(`5 0 obj\n<< /Length ${content.length} >>\nstream\n`, "ascii"),
        content,
        Buffer.from("\nendstream\nendobj\n", "ascii"),
      ]),
      Buffer.concat([
        Buffer.from(`6 0 obj\n<< /Length ${htmlComment.length} >>\nstream\n`, "ascii"),
        htmlComment,
        Buffer.from("\nendstream\nendobj\n", "ascii"),
      ]),
    ];

    const parts: Buffer[] = [Buffer.from([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34, 0x0a, 0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a])];
    let length = parts[0].length;
    const offsets: number[] = [];
    for (const obj of objects) {
      offsets.push(length);
      parts.push(obj);
      length += obj.length;
    }
    const xrefPos = length;
    parts.push(Buffer.from(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`, "ascii"));
    for (const off of offsets) {
      parts.push(Buffer.from(`${String(off).padStart(10, "0")} 00000 n \n`, "ascii"));
    }
    parts.push(
      Buffer.from(
        `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefPos}\n%%EOF\n`,
        "ascii"
      )
    );
    this.recordExport(user, report, "pdf");
    return Buffer.concat(parts);
  }

  recordExport(user: UserRow, report: Report, formatName: string): void {
    this.energy.store.reports.push({
      id: this.energy.store.nextId("report"),
      home_id: user.home_id,
      user_id: user.id,
      start_ms: report.start_ms,
      end_ms: report.end_ms,
      format: formatName,
      created_at_ms: nowMs(),
    });
  }
}
