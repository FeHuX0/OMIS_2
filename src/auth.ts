import crypto from "node:crypto";
import { AccessDenied, Role, Session, ValidationError, nowMs } from "./domain.js";
import { MemoryStore, UserRow } from "./store.js";

export function passwordHash(password: string): string {
  if (typeof password !== "string" || password.length < 8) {
    throw new ValidationError("Пароль должен содержать не менее 8 символов.");
  }
  const salt = crypto.randomBytes(16).toString("hex");
  const digest = crypto.pbkdf2Sync(password, Buffer.from(salt, "hex"), 260_000, 32, "sha256");
  return `pbkdf2_sha256$260000$${salt}$${digest.toString("hex")}`;
}

export function passwordMatches(password: string, encoded: string): boolean {
  const parts = encoded.split("$");
  if (parts.length !== 4) return false;
  const [, iterStr, salt, expected] = parts;
  const iterations = Number(iterStr);
  const actual = crypto.pbkdf2Sync(password, Buffer.from(salt, "hex"), iterations, 32, "sha256");
  const expectedBuf = Buffer.from(expected, "hex");
  if (actual.length !== expectedBuf.length) return false;
  return crypto.timingSafeEqual(actual, expectedBuf);
}

export class AccessControl {
  private sessions = new Map<string, number>();
  private failedAttempts = new Map<string, number[]>();

  constructor(private readonly store: MemoryStore) {}

  require(session: Session, owner = false): UserRow {
    const userId = this.sessions.get(session.token);
    const user = userId !== undefined ? this.store.users.get(userId) : undefined;
    if (!user || !user.active) {
      throw new AccessDenied("Сеанс завершён. Войдите снова.");
    }
    if (owner && user.role !== Role.OWNER) {
      throw new AccessDenied("Действие доступно только владельцу дома.");
    }
    return user;
  }

  login(login: string, password: string): Session {
    const key = (login ?? "").trim().toLowerCase();
    const currentMs = nowMs();
    const attempts = (this.failedAttempts.get(key) ?? []).filter((t) => currentMs - t < 60_000);
    if (attempts.length >= 5) {
      throw new AccessDenied("Слишком много попыток. Повторите вход через минуту.");
    }
    const user = this.store.usersByLogin.get(key);
    if (!user || !user.active || !passwordMatches(password ?? "", user.password)) {
      this.failedAttempts.set(key, [...attempts, currentMs]);
      this.store.logEvent(null, "login_failed", "Неуспешная попытка входа");
      throw new AccessDenied("Неверный логин или пароль.");
    }
    this.failedAttempts.delete(key);
    const token = crypto.randomBytes(32).toString("base64url");
    this.sessions.set(token, user.id);
    this.store.logEvent(user.home_id, "login", "Вход в систему", { user_id: user.id });
    return { token };
  }

  logout(session: Session): void {
    this.sessions.delete(session.token);
  }

  registerHome(
    name: string,
    login: string,
    password: string,
    homeName: string,
    address = "",
    kind: string = "apartment"
  ): Session {
    const cleanName = (name ?? "").trim();
    const cleanLogin = (login ?? "").trim().toLowerCase();
    const cleanHome = (homeName ?? "").trim();
    const cleanAddr = address ?? "";
    if (!cleanName || !cleanLogin || !cleanHome) {
      throw new ValidationError("Заполните имя, логин и название дома.");
    }
    if (Math.max(cleanName.length, cleanLogin.length, cleanHome.length, cleanAddr.length) > 200) {
      throw new ValidationError("Поля должны содержать не более 200 символов.");
    }
    if (kind !== "apartment" && kind !== "house") {
      throw new ValidationError("Выберите квартиру или частный дом.");
    }
    if (this.store.usersByLogin.has(cleanLogin)) {
      throw new ValidationError("Этот логин уже занят.");
    }
    const encoded = passwordHash(password);
    const homeId = this.store.nextId("home");
    this.store.homes.set(homeId, {
      id: homeId,
      name: cleanHome,
      address: cleanAddr,
      kind,
      timezone: "Europe/Minsk",
    });

    const userId = this.store.nextId("user");
    const userRow: UserRow = {
      id: userId,
      home_id: homeId,
      name: cleanName,
      login: cleanLogin,
      password: encoded,
      role: Role.OWNER,
      active: true,
      created_at_ms: nowMs(),
    };
    this.store.users.set(userId, userRow);
    this.store.usersByLogin.set(cleanLogin, userRow);

    this.store.settings.set(homeId, {
      home_id: homeId,
      monthly_budget: 100,
      daily_kwh: 25,
      notify_80: true,
      auto_protect: true,
      scenario: "manual",
    });

    const tariffId = this.store.nextId("tariff");
    this.store.tariffs.set(tariffId, {
      id: tariffId,
      home_id: homeId,
      effective_at_ms: 0,
      day_rate: 0.28,
      night_rate: 0.14,
      day_start: 7,
      night_start: 23,
    });

    this.store.logEvent(homeId, "home_created", "Зарегистрирован дом");
    return this.login(cleanLogin, password);
  }

  addFamily(session: Session, name: string, login: string, password: string): void {
    const user = this.require(session, true);
    const cleanName = (name ?? "").trim();
    const cleanLogin = (login ?? "").trim().toLowerCase();
    if (!cleanName || !cleanLogin || Math.max(cleanName.length, cleanLogin.length) > 200) {
      throw new ValidationError("Укажите имя и логин длиной до 200 символов.");
    }
    if (this.store.usersByLogin.has(cleanLogin)) {
      throw new ValidationError("Этот логин уже занят.");
    }
    const encoded = passwordHash(password);
    const userId = this.store.nextId("user");
    const row: UserRow = {
      id: userId,
      home_id: user.home_id,
      name: cleanName,
      login: cleanLogin,
      password: encoded,
      role: Role.FAMILY,
      active: true,
      created_at_ms: nowMs(),
    };
    this.store.users.set(userId, row);
    this.store.usersByLogin.set(cleanLogin, row);
    this.store.logEvent(user.home_id, "family_added", `Добавлен член семьи: ${cleanName}`);
  }

  family(session: Session): Array<{ id: number; name: string; login: string; role: Role; active: boolean }> {
    const user = this.require(session, true);
    const rows: Array<{ id: number; name: string; login: string; role: Role; active: boolean }> = [];
    for (const u of this.store.users.values()) {
      if (u.home_id === user.home_id) {
        rows.push({
          id: u.id,
          name: u.name,
          login: u.login,
          role: u.role,
          active: u.active,
        });
      }
    }
    return rows.sort((a, b) => a.id - b.id);
  }

  setFamilyActive(session: Session, userId: number, active: boolean): void {
    const user = this.require(session, true);
    const target = this.store.users.get(userId);
    if (!target || target.home_id !== user.home_id || target.role !== Role.FAMILY) {
      throw new ValidationError("Член семьи не найден.");
    }
    target.active = Boolean(active);
    this.store.logEvent(user.home_id, "access_changed", "Изменён доступ члена семьи");
  }
}
