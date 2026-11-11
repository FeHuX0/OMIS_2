from .domain import DeviceState, ValidationError, now


class AutomationService:
    """Правила и планировщик используют тот же автомат устройства, что и ручное управление."""

    def __init__(self, energy):
        self.energy = energy

    def apply(self, session, scenario):
        user = self.energy.access.require(session, owner=True)
        if scenario not in ("manual", "eco", "vacation", "night_tariff"):
            raise ValidationError("Неизвестный сценарий.")
        changed = 0
        for d in self.energy.devices(session):
            if d["state"] in (DeviceState.EMERGENCY, DeviceState.RETIRED) or d["essential"]:
                continue
            action = None
            if scenario == "vacation" and d["state"] != DeviceState.OFF:
                action = "disable"
            elif (
                scenario in ("eco", "night_tariff")
                and d["flexible"]
                and d["state"] in (DeviceState.IDLE, DeviceState.RUNNING)
            ):
                action = "schedule"
            elif scenario == "eco" and d["state"] == DeviceState.IDLE:
                action = "disable"
            if action:
                self.energy.change_state(session, d["id"], action)
                changed += 1
        with self.energy.db.transaction():
            self.energy.db.execute(
                "UPDATE settings SET scenario=? WHERE home_id=?", (scenario, user["home_id"])
            )
            self.energy.db.event(user["home_id"], "scenario", f"Применён сценарий: {scenario}")
            self.energy.notifications.send(
                user["home_id"],
                "scenario",
                f"Сценарий «{scenario}»: изменено приборов — {changed}.",
            )
        self.tick(session)
        return changed

    def tick(self, session):
        settings = self.energy.settings(session)
        hour = now().hour
        night = hour < settings["day_start"] or hour >= settings["night_start"]
        if not night:
            return 0
        changed = 0
        for device in self.energy.devices(session):
            if device["state"] == DeviceState.FLEXIBLE:
                self.energy.change_state(session, device["id"], "night")
                changed += 1
        return changed
