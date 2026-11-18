import { AutomationService } from "./automation.js";
import { DeviceState, Session, ValidationError } from "./domain.js";
import { ReportService } from "./reports.js";
import { EnergyService, numberValue } from "./services.js";

/** АдаптерУмногоДома (диаграмма 4.2.1). */
export interface SmartHomeAdapter {
  readonly platform: string;
  powerW(entityId: string): Promise<number>;
  switch(entityId: string, enabled: boolean): Promise<void>;
  sendCommand(device: string, cmd: string): Promise<string>;
  getEvent(): Promise<string>;
}

/** АдаптерHomeAssistant (диаграмма 4.2.1). */
export class HomeAssistantAdapter implements SmartHomeAdapter {
  readonly platform = "Home Assistant";
  private readonly baseUrl: string;
  private readonly token: string;

  constructor(baseUrl: string, token: string) {
    if (!baseUrl.startsWith("http://") && !baseUrl.startsWith("https://")) {
      throw new ValidationError("Укажите HTTP(S)-адрес Home Assistant и токен доступа.");
    }
    if (!(token ?? "").trim()) {
      throw new ValidationError("Укажите HTTP(S)-адрес Home Assistant и токен доступа.");
    }
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  private async request(path: string, payload?: unknown): Promise<Record<string, unknown>> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: payload !== undefined ? "POST" : "GET",
        headers: {
          Authorization: `Bearer ${this.token}`,
          "Content-Type": "application/json",
        },
        body: payload !== undefined ? JSON.stringify(payload) : undefined,
        signal: controller.signal,
      });
      if (!res.ok) {
        throw new ValidationError("Home Assistant недоступен или вернул неверный ответ.");
      }
      return (await res.json()) as Record<string, unknown>;
    } catch (err) {
      if (err instanceof ValidationError) throw err;
      throw new ValidationError("Home Assistant недоступен или вернул неверный ответ.");
    } finally {
      clearTimeout(timer);
    }
  }

  static validateEntity(entityId: string): string {
    const clean = (entityId ?? "").trim();
    if (!clean || !/^[a-z0-9_.]+$/.test(clean)) {
      throw new ValidationError("Некорректный идентификатор сущности Home Assistant.");
    }
    return clean;
  }

  async powerW(entityId: string): Promise<number> {
    const entity = HomeAssistantAdapter.validateEntity(entityId);
    const data = await this.request(`/api/states/${entity}`);
    const attrs = (data.attributes ?? {}) as Record<string, unknown>;
    const unit = attrs.unit_of_measurement;
    if (unit !== "W" && unit !== "kW") {
      throw new ValidationError("Датчик должен сообщать мощность в W или kW.");
    }
    const val = numberValue(data.state, "Мощность датчика", 0, 10_000_000);
    return unit === "kW" ? val * 1000 : val;
  }

  async switch(entityId: string, enabled: boolean): Promise<void> {
    const entity = HomeAssistantAdapter.validateEntity(entityId);
    if (!entity.startsWith("switch.")) {
      throw new ValidationError("Для управления нужна сущность switch.*.");
    }
    await this.request(`/api/services/switch/turn_${enabled ? "on" : "off"}`, {
      entity_id: entity,
    });
  }

  async sendCommand(device: string, cmd: string): Promise<string> {
    const enabled = cmd.toLowerCase() !== "off";
    await this.switch(device, enabled);
    return `Home Assistant: команда «${cmd}» передана на ${device}.`;
  }

  async getEvent(): Promise<string> {
    return "Home Assistant: ожидание событий телеметрии.";
  }
}

/** АдаптерЯндексАлиса (диаграмма 4.2.1). */
export class YandexAliceAdapter implements SmartHomeAdapter {
  readonly platform = "Яндекс.Алиса";

  constructor(private readonly skillId = "energy-home-alice") {}

  async powerW(_entityId: string): Promise<number> {
    throw new ValidationError("Облачный навык Яндекс.Алисы: подключите OAuth-токен навыка для чтения датчиков.");
  }

  async switch(entityId: string, enabled: boolean): Promise<void> {
    if (!entityId.trim()) throw new ValidationError("Укажите идентификатор устройства Алисы.");
    void enabled;
  }

  async sendCommand(device: string, cmd: string): Promise<string> {
    if (!device.trim()) throw new ValidationError("Укажите устройство в Яндекс.Алисе.");
    return `Яндекс.Алиса (${this.skillId}): подготовлена команда «${cmd}» для «${device}».`;
  }

  async getEvent(): Promise<string> {
    return "Яндекс.Алиса: голосовой webhook активен в режиме эмуляции.";
  }
}

/** АдаптерGoogleHome (диаграмма 4.2.1). */
export class GoogleHomeAdapter implements SmartHomeAdapter {
  readonly platform = "Google Home";

  constructor(private readonly projectId = "energy-home-assistant") {}

  async powerW(_entityId: string): Promise<number> {
    throw new ValidationError("Google Home Smart Home API: требуется привязка аккаунта Google для опроса устройств.");
  }

  async switch(entityId: string, enabled: boolean): Promise<void> {
    if (!entityId.trim()) throw new ValidationError("Укажите идентификатор устройства Google Home.");
    void enabled;
  }

  async sendCommand(device: string, cmd: string): Promise<string> {
    if (!device.trim()) throw new ValidationError("Укажите устройство в Google Home.");
    return `Google Home (${this.projectId}): сформировано действие «${cmd}» для «${device}».`;
  }

  async getEvent(): Promise<string> {
    return "Google Home: Report State готов к интеграции.";
  }
}

/** ГолосоваяКоманда (диаграмма 4.2.1). */
export interface VoiceCommand {
  text: string;
  intent: string;
}

/** ГолосовойАссистент (диаграмма 4.2.1). */
export class VoiceAssistant {
  constructor(
    private readonly energy: EnergyService,
    private readonly reports: ReportService,
    private readonly automation: AutomationService
  ) {}

  recognize(text: string): VoiceCommand {
    const cleaned = (text ?? "").trim().toLowerCase();
    const commands: Record<string, string> = {
      "текущее потребление": "current",
      "отчёт за месяц": "month",
      "отчет за месяц": "month",
      "эко-режим": "eco",
      "отпуск": "vacation",
      "ночной тариф": "night_tariff",
      "выключить приборы": "disable_idle",
      "статус уведомлений": "alerts",
    };
    const intent = commands[cleaned];
    if (!intent) {
      throw new ValidationError(
        "Команды: текущее потребление; отчёт за месяц; эко-режим; отпуск; ночной тариф; выключить приборы; статус уведомлений."
      );
    }
    return { text, intent };
  }

  execute(session: Session, command: VoiceCommand): string {
    this.energy.access.require(session);
    if (command.intent === "current") {
      const dash = this.reports.dashboard(session);
      return `Сегодня израсходовано ${dash.daily_kwh.toFixed(2)} киловатт-часа.`;
    }
    if (command.intent === "month") {
      const rep = this.reports.build(session);
      return `За месяц: ${rep.energy_kwh.toFixed(2)} киловатт-часа, ${rep.cost.toFixed(2)} BYN.`;
    }
    if (command.intent === "alerts") {
      const unread = this.energy.notifications.list(session, 20).filter((n) => !n.is_read);
      return unread.length > 0
        ? `Есть непрочитанные уведомления (${unread.length}): ${unread[0].message}`
        : "Новых предупреждений о лимитах и аномалиях нет.";
    }
    if (command.intent === "disable_idle") {
      let count = 0;
      for (const d of this.energy.devices(session)) {
        if (!d.essential && d.state === DeviceState.IDLE) {
          this.energy.changeState(session, d.id, "disable");
          count += 1;
        }
      }
      return `Неиспользуемые приборы отключены: ${count}.`;
    }
    const changed = this.automation.apply(session, command.intent);
    return `Сценарий применён. Изменено приборов: ${changed}.`;
  }
}

/** ОбщаяЗона (диаграмма 4.2.2: тип, получитьРасход()). */
export interface CommonZoneRow {
  id: number;
  home_id: number;
  zone_type: string;
  kwh: number;
}

/** Изолированный учёт многоквартирного дома и общих зон (диаграмма 4.2.2). */
export class BuildingService {
  private zonesByHome = new Map<number, CommonZoneRow[]>();
  private zoneSeq = 0;

  constructor(private readonly energy: EnergyService) {}

  private ensureDefaultZones(homeId: number): CommonZoneRow[] {
    let zones = this.zonesByHome.get(homeId);
    if (!zones) {
      zones = [
        { id: ++this.zoneSeq, home_id: homeId, zone_type: "Подъезд", kwh: 35 },
        { id: ++this.zoneSeq, home_id: homeId, zone_type: "Лифт", kwh: 45 },
        { id: ++this.zoneSeq, home_id: homeId, zone_type: "Парковка", kwh: 20 },
      ];
      this.zonesByHome.set(homeId, zones);
    }
    return zones;
  }

  commonZones(session: Session): CommonZoneRow[] {
    const user = this.energy.access.require(session);
    return this.ensureDefaultZones(user.home_id);
  }

  addCommonZone(session: Session, zoneType: string, kwh: number): CommonZoneRow {
    const user = this.energy.access.require(session, true);
    const cleanType = (zoneType ?? "").trim();
    if (!cleanType || cleanType.length > 60) {
      throw new ValidationError("Укажите тип общей зоны (например, Подъезд, Лифт, Парковка).");
    }
    const cleanKwh = numberValue(kwh, "Расход общей зоны", 0);
    const zones = this.ensureDefaultZones(user.home_id);
    const existing = zones.find((z) => z.zone_type.toLowerCase() === cleanType.toLowerCase());
    if (existing) {
      existing.kwh = cleanKwh;
      return existing;
    }
    const row: CommonZoneRow = {
      id: ++this.zoneSeq,
      home_id: user.home_id,
      zone_type: cleanType,
      kwh: cleanKwh,
    };
    zones.push(row);
    return row;
  }

  totalCommonConsumption(session: Session): number {
    return this.commonZones(session).reduce((sum, z) => sum + z.kwh, 0);
  }

  apartments(session: Session) {
    const user = this.energy.access.require(session);
    const building = this.energy.store.buildingsByHome.get(user.home_id);
    if (!building) return [];
    return this.energy.store.apartments
      .filter((a) => a.building_id === building.id)
      .sort((a, b) => a.id - b.id);
  }

  addApartment(session: Session, name: string, area: number, residents: number, ownKwh: number): void {
    const user = this.energy.access.require(session, true);
    const cleanArea = numberValue(area, "Площадь", 0.1);
    const cleanOwnKwh = numberValue(ownKwh, "Потребление квартиры", 0);
    if (!Number.isInteger(residents) || residents < 1 || residents > 100) {
      throw new ValidationError("Число жильцов: от 1 до 100.");
    }
    const cleanName = (name ?? "").trim();
    if (!cleanName || cleanName.length > 100) {
      throw new ValidationError("Укажите название квартиры длиной до 100 символов.");
    }
    let building = this.energy.store.buildingsByHome.get(user.home_id);
    if (!building) {
      building = {
        id: this.energy.store.nextId("building"),
        home_id: user.home_id,
        name: "Многоквартирный дом",
      };
      this.energy.store.buildingsByHome.set(user.home_id, building);
    }
    const exists = this.energy.store.apartments.some(
      (a) => a.building_id === building!.id && a.name.toLowerCase() === cleanName.toLowerCase()
    );
    if (exists) {
      throw new ValidationError("Квартира с таким названием уже существует.");
    }
    this.energy.store.apartments.push({
      id: this.energy.store.nextId("apartment"),
      building_id: building.id,
      name: cleanName,
      area: cleanArea,
      residents,
      own_kwh: cleanOwnKwh,
    });
  }

  distribute(session: Session, commonKwh: number | null, method = "area") {
    const apartments = this.apartments(session);
    const totalCommon =
      commonKwh !== null && commonKwh !== undefined && Number.isFinite(commonKwh)
        ? numberValue(commonKwh, "Расход общих зон", 0)
        : this.totalCommonConsumption(session);
    if (method !== "area" && method !== "residents" && method !== "equal") {
      throw new ValidationError("Правила: по площади, по числу жильцов или поровну.");
    }
    if (apartments.length === 0) return [];
    const weights = apartments.map((a) => (method === "equal" ? 1 : method === "area" ? a.area : a.residents));
    const totalWeight = weights.reduce((acc, w) => acc + w, 0);
    const totalArea = apartments.reduce((acc, a) => acc + a.area, 0);
    const totalOwn = apartments.reduce((acc, a) => acc + a.own_kwh, 0);
    const average = totalArea > 0 ? totalOwn / totalArea : 0;

    return apartments.map((apt, idx) => {
      const share = (totalCommon * weights[idx]) / totalWeight;
      const intensity = apt.own_kwh / apt.area;
      const efficiencyPercent = average > 0 ? (1 - intensity / average) * 100 : 0;
      const ratingScore = Math.max(0, Math.min(100, Math.round(50 + efficiencyPercent)));
      return {
        ...apt,
        common_kwh: share,
        total_kwh: apt.own_kwh + share,
        efficiency_percent: efficiencyPercent,
        rating_score: ratingScore,
      };
    });
  }
}
