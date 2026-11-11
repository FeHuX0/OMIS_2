import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime

from .domain import AccessDenied, Role, Session, ValidationError, now, stamp
from .storage import Database


def password_hash(password: str) -> str:
    if len(password) < 8:
        raise ValidationError("Пароль должен содержать не менее 8 символов.")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 260_000)
    return f"pbkdf2_sha256$260000${salt}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    _, iterations, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
    ).hex()
    return hmac.compare_digest(actual, expected)


class AccessControl:
    def __init__(self, db: Database):
        self.db = db
        self.sessions: dict[str, int] = {}
        self.failed_attempts: dict[str, list[datetime]] = {}

    def require(self, session: Session, *, owner=False) -> dict:
        user = self.db.one(
            "SELECT id,home_id,name,login,role,active FROM users WHERE id=? AND active=1",
            (self.sessions.get(session.token),),
        )
        if not user:
            raise AccessDenied("Сеанс завершён. Войдите снова.")
        if owner and user["role"] != Role.OWNER:
            raise AccessDenied("Действие доступно только владельцу дома.")
        return user

    def login(self, login: str, password: str) -> Session:
        key = login.strip().casefold()
        attempts = [
            t for t in self.failed_attempts.get(key, []) if (now() - t).total_seconds() < 60
        ]
        if len(attempts) >= 5:
            raise AccessDenied("Слишком много попыток. Повторите вход через минуту.")
        user = self.db.one("SELECT * FROM users WHERE login=? AND active=1", (key,))
        if not user or not password_matches(password, user["password"]):
            self.failed_attempts[key] = [*attempts, now()]
            with self.db.transaction():
                self.db.event(None, "login_failed", "Неуспешная попытка входа")
            raise AccessDenied("Неверный логин или пароль.")
        self.failed_attempts.pop(key, None)
        token = secrets.token_urlsafe(32)
        self.sessions[token] = user["id"]
        with self.db.transaction():
            self.db.event(user["home_id"], "login", "Вход в систему", user_id=user["id"])
        return Session(token)

    def logout(self, session: Session):
        self.sessions.pop(session.token, None)

    def register_home(
        self, name, login, password, home_name, address="", kind="apartment"
    ) -> Session:
        name, login, home_name = name.strip(), login.strip().casefold(), home_name.strip()
        if not all((name, login, home_name)):
            raise ValidationError("Заполните имя, логин и название дома.")
        if max(map(len, (name, login, home_name, address))) > 200:
            raise ValidationError("Поля должны содержать не более 200 символов.")
        if kind not in ("apartment", "house"):
            raise ValidationError("Выберите квартиру или частный дом.")
        encoded = password_hash(password)
        try:
            with self.db.transaction():
                home_id = self.db.execute(
                    "INSERT INTO homes(name,address,kind) VALUES(?,?,?)", (home_name, address, kind)
                ).lastrowid
                self.db.execute(
                    "INSERT INTO users(home_id,name,login,password,role,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (home_id, name, login, encoded, Role.OWNER, stamp(now())),
                )
                self.db.execute("INSERT INTO settings(home_id) VALUES(?)", (home_id,))
                self.db.execute(
                    "INSERT INTO tariffs(home_id,effective_at,day_rate,night_rate,"
                    "day_start,night_start) VALUES(?,?,?,?,?,?)",
                    (home_id, "1970-01-01T00:00:00.000000+00:00", 0.28, 0.14, 7, 23),
                )
                self.db.event(home_id, "home_created", "Зарегистрирован дом")
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Этот логин уже занят.") from exc
        return self.login(login, password)

    def add_family(self, session, name, login, password):
        user = self.require(session, owner=True)
        name, login = name.strip(), login.strip().casefold()
        if not name or not login or max(len(name), len(login)) > 200:
            raise ValidationError("Укажите имя и логин длиной до 200 символов.")
        encoded = password_hash(password)
        try:
            with self.db.transaction():
                self.db.execute(
                    "INSERT INTO users(home_id,name,login,password,role,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (user["home_id"], name, login, encoded, Role.FAMILY, stamp(now())),
                )
                self.db.event(user["home_id"], "family_added", f"Добавлен член семьи: {name}")
        except sqlite3.IntegrityError as exc:
            raise ValidationError("Этот логин уже занят.") from exc

    def family(self, session):
        user = self.require(session, owner=True)
        return self.db.all(
            "SELECT id,name,login,role,active FROM users WHERE home_id=? ORDER BY id",
            (user["home_id"],),
        )

    def set_family_active(self, session, user_id, active):
        user = self.require(session, owner=True)
        with self.db.transaction():
            result = self.db.execute(
                "UPDATE users SET active=? WHERE id=? AND home_id=? AND role='family'",
                (bool(active), user_id, user["home_id"]),
            )
            if result.rowcount != 1:
                raise ValidationError("Член семьи не найден.")
            self.db.event(user["home_id"], "access_changed", "Изменён доступ члена семьи")
