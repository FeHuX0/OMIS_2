import csv
import html
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .domain import LOCAL_TZ, Report, ValidationError, now, stamp


def period_bounds(period: str, anchor: datetime):
    anchor = anchor.astimezone(LOCAL_TZ)
    day = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day, day + timedelta(days=1), day - timedelta(days=1)
    if period == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7), start - timedelta(days=7)
    if period == "month":
        start = day.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        previous = (start - timedelta(days=1)).replace(day=1)
        return start, end, previous
    raise ValidationError("Выберите день, неделю или месяц.")


def segments(db, home_id, start, end, room_id=None, device_id=None):
    sql = """
    SELECT s.*, a.name, a.flexible, r.name AS room, d.id AS device_id
    FROM reading_segments s JOIN readings v ON v.id=s.reading_id
    JOIN devices d ON d.id=v.device_id JOIN appliances a ON a.id=d.appliance_id
    JOIN rooms r ON r.id=a.room_id
    WHERE r.home_id=? AND d.counted=1 AND s.start_at<? AND s.end_at>?
    """
    params = [home_id, stamp(end), stamp(start)]
    if room_id is not None:
        sql += " AND r.id=?"
        params.append(room_id)
    if device_id is not None:
        sql += " AND d.id=?"
        params.append(device_id)
    return db.all(sql, params)


def clipped(row, start, end):
    left, right = datetime.fromisoformat(row["start_at"]), datetime.fromisoformat(row["end_at"])
    fraction = (
        max(0, (min(end, right) - max(start, left)).total_seconds())
        / (right - left).total_seconds()
    )
    return row["energy_kwh"] * fraction, row["cost"] * fraction


def totals(db, home_id, start, end, room_id=None, device_id=None):
    energy = cost = 0.0
    for row in segments(db, home_id, start, end, room_id, device_id):
        e, c = clipped(row, start, end)
        energy += e
        cost += c
    return energy, cost


class ReportService:
    def __init__(self, db, access):
        self.db, self.access = db, access

    def build(self, session, period="month", anchor=None, room_id=None, device_id=None):
        user = self.access.require(session)
        start, full_end, previous_start = period_bounds(period, anchor or now())
        # Текущий незавершённый период сравнивается с такой же прошедшей частью предыдущего.
        end = min(full_end, now())
        if end <= start:
            raise ValidationError("Период ещё не начался.")
        previous_end = start if end == full_end else min(start, previous_start + (end - start))
        previous, _ = totals(
            self.db, user["home_id"], previous_start, previous_end, room_id, device_id
        )
        data = segments(self.db, user["home_id"], start, end, room_id, device_id)
        buckets = {}
        cursor = start
        step = timedelta(hours=1) if period == "day" else timedelta(days=1)
        while cursor < end:
            buckets[cursor] = 0.0
            cursor += step
        energy = cost = 0.0
        by_device = defaultdict(lambda: {"energy_kwh": 0.0, "cost": 0.0})
        flexible_energy = 0.0
        for row in data:
            e, c = clipped(row, start, end)
            energy += e
            cost += c
            if row["flexible"]:
                flexible_energy += e
            device = by_device[row["device_id"]]
            device.update(name=row["name"], room=row["room"])
            device["energy_kwh"] += e
            device["cost"] += c
            left = max(start, datetime.fromisoformat(row["start_at"])).astimezone(LOCAL_TZ)
            bucket = left.replace(minute=0, second=0, microsecond=0)
            if period != "day":
                bucket = bucket.replace(hour=0)
            # Сегменты разделены по часам, поэтому не пересекают границы корзин.
            if bucket in buckets:
                buckets[bucket] += e
        top = sorted(by_device.values(), key=lambda d: d["energy_kwh"], reverse=True)
        anomaly_sql = """
        SELECT n.*, a.name, r.name AS room FROM anomalies n
        JOIN devices d ON d.id=n.device_id JOIN appliances a ON a.id=d.appliance_id
        JOIN rooms r ON r.id=a.room_id WHERE r.home_id=? AND n.created_at>=? AND n.created_at<?
        """
        params = [user["home_id"], stamp(start), stamp(end)]
        if room_id is not None:
            anomaly_sql += " AND r.id=?"
            params.append(room_id)
        if device_id is not None:
            anomaly_sql += " AND d.id=?"
            params.append(device_id)
        anomalies = self.db.all(anomaly_sql + " ORDER BY n.created_at DESC", params)
        recommendations = []
        if top and energy:
            recommendations.append(
                f"Наибольший вклад: {top[0]['name']} — "
                f"{top[0]['energy_kwh'] / energy * 100:.0f}% расхода. "
                "Проверьте время работы и настройки прибора."
            )
        if flexible_energy:
            recommendations.append(
                f"Гибкая нагрузка: {flexible_energy:.2f} кВт·ч. "
                "Перенесите стирку или зарядку на часы низкого тарифа."
            )
        if anomalies:
            recommendations.append("Выявлены аномалии. Проверьте приборы до сброса аварии.")
        change = (energy / previous - 1) * 100 if previous > 0 else None
        if change is not None and change > 15:
            recommendations.append(
                f"Расход вырос на {change:.1f}%. Сравните режимы работы приборов."
            )
        if not data:
            recommendations.append(
                "За выбранный период нет показаний. Проверьте подключение устройств."
            )
        labels = [
            (t.strftime("%H:%M" if period == "day" else "%d.%m"), round(v, 5))
            for t, v in buckets.items()
        ]
        return Report(
            start, end, energy, cost, previous, change, labels, top, anomalies, recommendations
        )

    def dashboard(self, session, room_id=None, device_id=None):
        user = self.access.require(session)
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        daily, daily_cost = totals(self.db, user["home_id"], today, now(), room_id, device_id)
        monthly, monthly_cost = totals(
            self.db, user["home_id"], today.replace(day=1), now(), room_id, device_id
        )
        return dict(
            daily_kwh=daily, daily_cost=daily_cost, monthly_kwh=monthly, monthly_cost=monthly_cost
        )

    def export_csv(self, session, report: Report, path: Path):
        user = self.access.require(session)
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(
                ["Тип", "Период / прибор", "Комната", "Энергия, кВт·ч", "Стоимость, BYN"]
            )
            writer.writerow(
                [
                    "Итого",
                    f"{report.start:%d.%m.%Y} — {report.end:%d.%m.%Y}",
                    "",
                    f"{report.energy_kwh:.5f}",
                    f"{report.cost:.5f}",
                ]
            )
            for label, energy in report.series:
                writer.writerow(["График", label, "", f"{energy:.5f}", ""])
            for device in report.devices:
                writer.writerow(
                    [
                        "Прибор",
                        self._safe_cell(device["name"]),
                        self._safe_cell(device["room"]),
                        f"{device['energy_kwh']:.5f}",
                        f"{device['cost']:.5f}",
                    ]
                )
        self.record_export(user, report, "csv")

    @staticmethod
    def _safe_cell(value):
        # Названия, введённые пользователем, не должны становиться формулами Excel.
        return "'" + value if value[:1] in ("=", "+", "-", "@") else value

    def html(self, session, report):
        self.access.require(session)
        rows = "".join(
            f"<tr><td>{html.escape(d['name'])}</td><td>{html.escape(d['room'])}</td>"
            f"<td>{d['energy_kwh']:.3f}</td><td>{d['cost']:.2f}</td></tr>"
            for d in report.devices
        )
        recs = "".join(f"<li>{html.escape(r)}</li>" for r in report.recommendations)
        history = "".join(
            f"<tr><td>{html.escape(label)}</td><td>{value:.3f}</td></tr>"
            for label, value in report.series
        )
        anomalies = "".join(f"<li>{html.escape(a['message'])}</li>" for a in report.anomalies)
        change = (
            f"{report.change_percent:+.1f}%"
            if report.change_percent is not None
            else "недостаточно данных для сравнения"
        )
        return (
            "<html><head><meta charset='utf-8'></head><body style='font-family:sans-serif'>"
            f"<h1>Energy Home — отчёт</h1><p>{report.start:%d.%m.%Y %H:%M} — "
            f"{report.end:%d.%m.%Y %H:%M} (Минск)</p><h2>{report.energy_kwh:.3f} кВт·ч · "
            f"{report.cost:.2f} BYN</h2><p>Предыдущий сопоставимый период: "
            f"{report.previous_kwh:.3f} кВт·ч. Изменение: {change}.</p>"
            "<table border='1' cellpadding='8'>"
            f"<tr><th>Прибор</th><th>Комната</th><th>кВт·ч</th><th>BYN</th></tr>{rows}</table>"
            f"<h2>Динамика расхода</h2><table border='1' cellpadding='5'>"
            f"<tr><th>Период</th><th>кВт·ч</th></tr>{history}</table>"
            f"<h2>Аномалии</h2><ul>{anomalies or '<li>Не обнаружены</li>'}</ul>"
            f"<h2>Рекомендации</h2><ul>{recs}</ul></body></html>"
        )

    def record_export(self, user, report, format_name):
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO reports(home_id,user_id,start_at,end_at,format,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    user["home_id"],
                    user["id"],
                    stamp(report.start),
                    stamp(report.end),
                    format_name,
                    stamp(now()),
                ),
            )
