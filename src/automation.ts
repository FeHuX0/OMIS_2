import { DeviceState, Session, ValidationError, getLocalParts, nowMs } from "./domain.js";
import { EnergyService } from "./services.js";

/** Правило (диаграмма 4.2.3: условие, действие). */
export interface Rule {
  id: number;
  home_id: number;
  condition: string;
  action: string;
  enabled: boolean;
}

/** МодульУправленияУстройствами (диаграмма 4.2.3: отключить(прибор), включить(прибор)). */
export class DeviceControlModule {
  constructor(private readonly energy: EnergyService) {}

  turnOff(session: Session, deviceId: number): DeviceState {
    return this.energy.changeState(session, deviceId, "disable");
  }

  turnOn(session: Session, deviceId: number): DeviceState {
    const dev = this.energy.device(session, deviceId, true);
    if (dev.state === DeviceState.OFF) {
      return this.energy.changeState(session, deviceId, "enable");
    }
    if (dev.state === DeviceState.IDLE) {
      return this.energy.changeState(session, deviceId, "start");
    }
    return dev.state;
  }
}

/** ПланировщикНагрузки (диаграмма 4.2.3: перенестиНагрузку(прибор, время)). */
export class LoadScheduler {
  constructor(private readonly energy: EnergyService) {}

  rescheduleLoad(session: Session, deviceId: number, targetTime = "ночной тариф"): DeviceState {
    const dev = this.energy.device(session, deviceId, true);
    const nextState = this.energy.changeState(session, deviceId, "schedule");
    this.energy.store.logEvent(
      dev.home_id,
      "load_scheduled",
      `Нагрузка прибора «${dev.name}» перенесена на ${targetTime}`,
      { device_id: deviceId }
    );
    return nextState;
  }
}

/** ПодсистемаПравил и Сценарий (диаграмма 4.2.3). */
export class AutomationService {
  readonly controller: DeviceControlModule;
  readonly scheduler: LoadScheduler;
  private rulesByHome = new Map<number, Rule[]>();
  private ruleSeq = 0;

  constructor(private readonly energy: EnergyService) {
    this.controller = new DeviceControlModule(energy);
    this.scheduler = new LoadScheduler(energy);
  }

  private ensureDefaultRules(homeId: number): Rule[] {
    let list = this.rulesByHome.get(homeId);
    if (!list) {
      list = [
        {
          id: ++this.ruleSeq,
          home_id: homeId,
          condition: "power_w > critical_w (критический порог)",
          action: "Аварийно отключить прибор и отправить уведомление",
          enabled: true,
        },
        {
          id: ++this.ruleSeq,
          home_id: homeId,
          condition: "наступила ночная тарифная зона",
          action: "Запустить приборы в состоянии гибкой нагрузки",
          enabled: true,
        },
        {
          id: ++this.ruleSeq,
          home_id: homeId,
          condition: "активирован эко-режим или отпуск",
          action: "Отключить ожидающие некритичные приборы",
          enabled: true,
        },
      ];
      this.rulesByHome.set(homeId, list);
    }
    return list;
  }

  rules(session: Session): Rule[] {
    const user = this.energy.access.require(session);
    return this.ensureDefaultRules(user.home_id);
  }

  addRule(session: Session, condition: string, action: string): Rule {
    const user = this.energy.access.require(session, true);
    const cleanCond = (condition ?? "").trim();
    const cleanAct = (action ?? "").trim();
    if (!cleanCond || !cleanAct) {
      throw new ValidationError("Укажите условие и действие правила.");
    }
    const list = this.ensureDefaultRules(user.home_id);
    const rule: Rule = {
      id: ++this.ruleSeq,
      home_id: user.home_id,
      condition: cleanCond,
      action: cleanAct,
      enabled: true,
    };
    list.push(rule);
    this.energy.store.logEvent(user.home_id, "rule_added", `Добавлено правило: ${cleanCond}`);
    return rule;
  }

  checkRule(session: Session, eventType: string): boolean {
    const user = this.energy.access.require(session);
    const list = this.ensureDefaultRules(user.home_id);
    return list.some((r) => r.enabled && r.condition.toLowerCase().includes(eventType.toLowerCase()));
  }

  apply(session: Session, scenario: string): number {
    const user = this.energy.access.require(session, true);
    if (!["manual", "eco", "vacation", "night_tariff"].includes(scenario)) {
      throw new ValidationError("Неизвестный сценарий.");
    }
    let changed = 0;
    for (const d of this.energy.devices(session)) {
      if (d.state === DeviceState.EMERGENCY || d.state === DeviceState.RETIRED || d.essential) {
        continue;
      }
      if (scenario === "vacation" && d.state !== DeviceState.OFF) {
        this.controller.turnOff(session, d.id);
        changed += 1;
      } else if (
        (scenario === "eco" || scenario === "night_tariff") &&
        d.flexible &&
        (d.state === DeviceState.IDLE || d.state === DeviceState.RUNNING)
      ) {
        this.scheduler.rescheduleLoad(session, d.id, "ночной тариф");
        changed += 1;
      } else if (scenario === "eco" && d.state === DeviceState.IDLE) {
        this.controller.turnOff(session, d.id);
        changed += 1;
      }
    }
    const settings = this.energy.store.settings.get(user.home_id);
    if (settings) {
      settings.scenario = scenario;
    }
    this.energy.store.logEvent(user.home_id, "scenario", `Применён сценарий: ${scenario}`);
    this.energy.notifications.send(
      user.home_id,
      "scenario",
      `Сценарий «${scenario}»: изменено приборов — ${changed}.`
    );
    this.tick(session);
    return changed;
  }

  tick(session: Session): number {
    const settings = this.energy.settings(session, false);
    const hour = getLocalParts(nowMs()).hour;
    const night = hour < settings.day_start || hour >= settings.night_start;
    if (!night) return 0;
    let changed = 0;
    for (const device of this.energy.devices(session)) {
      if (device.state === DeviceState.FLEXIBLE) {
        this.energy.changeState(session, device.id, "night");
        changed += 1;
      }
    }
    return changed;
  }
}
