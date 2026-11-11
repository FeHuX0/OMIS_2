import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..automation import AutomationService
from ..demo import DEMO_PASSWORD, seed_demo
from ..domain import (
    LOCAL_TZ,
    STATE_LABELS,
    AccessDenied,
    DomainError,
    Navigation,
    ReadingPacket,
    Role,
    Screen,
    ValidationError,
    now,
)
from ..extensions import BuildingService, VoiceAssistant
from ..gateways import SensorGateway, Simulator, import_csv


class TkAppWindow:
    """Резервный настольный интерфейс на базе стандартного tkinter/ttk."""

    def __init__(self, energy, reports, *, demo: bool = False):
        self.energy = energy
        self.reports = reports
        self.access = energy.access
        self.automation = AutomationService(energy)
        self.building = BuildingService(energy.db, self.access)
        self.assistant = VoiceAssistant(energy, reports, self.automation)
        self.gateway = SensorGateway(energy)
        self.simulator = Simulator(energy, self.gateway)
        self.navigation = Navigation()
        self.session = None
        self.user = None
        self.current_report = None

        self.root = tk.Tk()
        self.root.title("Energy Home — учёт энергопотребления")
        self.root.geometry("1240x900")
        self.root.minsize(1000, 720)
        self.root.configure(bg="#f3f6f3")

        self._configure_styles()

        self.header = tk.Frame(
            self.root, bg="white", highlightbackground="#dce5df", highlightthickness=1
        )
        self._build_header()

        self.container = tk.Frame(self.root, bg="#f3f6f3")
        self.container.pack(fill="both", expand=True)

        self.pages: dict[Screen, tk.Frame] = {}
        self._build_login()
        self._build_home()
        self._build_devices()
        self._build_reports()
        self._build_settings()

        if demo:
            self.login_name_var.set("owner@demo.local")
            self.login_pass_var.set(DEMO_PASSWORD)
            self.demo_hint_var.set(f"Демо: owner@demo.local / {DEMO_PASSWORD}")

        self._show_screen(Screen.LOGIN)
        self._schedule_tick()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#eaf1eb")

    def guard(self, action):
        try:
            return action()
        except AccessDenied as exc:
            messagebox.showwarning("Доступ", str(exc), parent=self.root)
            if self.session:
                try:
                    self.access.require(self.session)
                except AccessDenied:
                    self.logout()
        except (DomainError, OSError, ValueError) as exc:
            messagebox.showwarning("Не удалось выполнить действие", str(exc), parent=self.root)

    def _build_header(self) -> None:
        brand = tk.Label(
            self.header,
            text="ϟ  Energy Home",
            font=("Segoe UI", 16, "bold"),
            fg="#147b63",
            bg="white",
        )
        brand.pack(side="left", padx=(24, 24), pady=12)

        self.nav_buttons: dict[Screen, tk.Button] = {}
        for title, screen in [
            ("Обзор", Screen.HOME),
            ("Устройства", Screen.DEVICES),
            ("Отчёты", Screen.REPORTS),
            ("Настройки", Screen.SETTINGS),
        ]:
            btn = tk.Button(
                self.header,
                text=title,
                font=("Segoe UI", 10, "bold"),
                bg="white",
                fg="#203b38",
                relief="solid",
                bd=1,
                padx=14,
                pady=6,
                command=lambda target=screen: self.guard(lambda: self.navigate(target)),
            )
            btn.pack(side="left", padx=4, pady=12)
            self.nav_buttons[screen] = btn

        logout_btn = tk.Button(
            self.header,
            text="Выйти",
            font=("Segoe UI", 10, "bold"),
            bg="white",
            fg="#203b38",
            relief="solid",
            bd=1,
            padx=14,
            pady=6,
            command=self.logout,
        )
        logout_btn.pack(side="right", padx=(8, 24), pady=12)

        self.profile_var = tk.StringVar(value="")
        profile_lbl = tk.Label(
            self.header,
            textvariable=self.profile_var,
            font=("Segoe UI", 10),
            fg="#203b38",
            bg="white",
        )
        profile_lbl.pack(side="right", padx=8, pady=12)

    def _show_screen(self, screen: Screen) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[screen].pack(fill="both", expand=True)
        for key, btn in self.nav_buttons.items():
            if key == screen:
                btn.configure(bg="#e3f1e7", fg="#147b63")
            else:
                btn.configure(bg="white", fg="#203b38")

    def _build_login(self) -> None:
        page = tk.Frame(self.container, bg="#f3f6f3")
        self.pages[Screen.LOGIN] = page

        card = tk.Frame(
            page,
            bg="white",
            highlightbackground="#dce5df",
            highlightthickness=1,
            padx=36,
            pady=32,
        )
        card.place(relx=0.5, rely=0.5, anchor="center", width=460)

        tk.Label(
            card,
            text="ϟ  Energy Home",
            font=("Segoe UI", 16, "bold"),
            fg="#147b63",
            bg="white",
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        tk.Label(
            card,
            text="Энергия под контролем",
            font=("Segoe UI", 19, "bold"),
            fg="#203b38",
            bg="white",
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            card,
            text="Потребление, расходы и забота о вашем доме.",
            font=("Segoe UI", 10),
            fg="#72867f",
            bg="white",
            anchor="w",
        ).pack(fill="x", pady=(2, 16))

        tk.Label(
            card, text="Логин / E-mail", font=("Segoe UI", 10), fg="#203b38", bg="white", anchor="w"
        ).pack(fill="x")
        self.login_name_var = tk.StringVar()
        tk.Entry(card, textvariable=self.login_name_var, font=("Segoe UI", 11)).pack(
            fill="x", pady=(4, 12), ipady=5
        )

        tk.Label(
            card, text="Пароль", font=("Segoe UI", 10), fg="#203b38", bg="white", anchor="w"
        ).pack(fill="x")
        self.login_pass_var = tk.StringVar()
        pass_entry = tk.Entry(
            card, textvariable=self.login_pass_var, show="*", font=("Segoe UI", 11)
        )
        pass_entry.pack(fill="x", pady=(4, 8), ipady=5)
        pass_entry.bind("<Return>", lambda _e: self.login())

        self.login_error_var = tk.StringVar()
        tk.Label(
            card,
            textvariable=self.login_error_var,
            font=("Segoe UI", 10),
            fg="#b64634",
            bg="white",
            anchor="w",
            wraplength=380,
        ).pack(fill="x", pady=(0, 8))

        tk.Button(
            card,
            text="Войти",
            font=("Segoe UI", 10, "bold"),
            bg="#187b63",
            fg="white",
            relief="flat",
            pady=8,
            command=self.login,
        ).pack(fill="x", pady=4)
        tk.Button(
            card,
            text="Зарегистрировать свой дом",
            font=("Segoe UI", 10, "bold"),
            bg="white",
            fg="#203b38",
            relief="solid",
            bd=1,
            pady=8,
            command=lambda: self.guard(self.register),
        ).pack(fill="x", pady=4)
        tk.Button(
            card,
            text="Открыть демонстрационный дом",
            font=("Segoe UI", 10, "bold"),
            bg="white",
            fg="#203b38",
            relief="solid",
            bd=1,
            pady=8,
            command=lambda: self.guard(self.open_demo),
        ).pack(fill="x", pady=(8, 6))

        self.demo_hint_var = tk.StringVar(value="Данные хранятся локально на вашем компьютере.")
        tk.Label(
            card,
            textvariable=self.demo_hint_var,
            font=("Segoe UI", 9),
            fg="#72867f",
            bg="white",
            anchor="w",
        ).pack(fill="x")

    def _build_home(self) -> None:
        page = tk.Frame(self.container, bg="#f3f6f3", padx=28, pady=20)
        self.pages[Screen.HOME] = page

        top = tk.Frame(page, bg="#f3f6f3")
        top.pack(fill="x", pady=(0, 12))
        self.home_title_var = tk.StringVar(value="Ваш дом сегодня")
        tk.Label(
            top,
            textvariable=self.home_title_var,
            font=("Segoe UI", 18, "bold"),
            fg="#203b38",
            bg="#f3f6f3",
        ).pack(side="left")

        self.simulate_var = tk.BooleanVar(value=False)
        self.simulate_chk = tk.Checkbutton(
            top,
            text="Симулятор",
            variable=self.simulate_var,
            bg="#f3f6f3",
            font=("Segoe UI", 10),
        )
        self.simulate_chk.pack(side="right", padx=8)

        metrics_row = tk.Frame(page, bg="#f3f6f3")
        metrics_row.pack(fill="x", pady=8)
        self.metric_vars: dict[str, tk.StringVar] = {}
        for key, title, hint in [
            ("power", "Текущая мощность", "По устройствам на связи"),
            ("daily", "Расход за сегодня", "С полуночи · Минск"),
            ("cost", "Расходы за месяц", "С учётом тарифных зон"),
        ]:
            card = tk.Frame(
                metrics_row,
                bg="white",
                highlightbackground="#dce5df",
                highlightthickness=1,
                padx=18,
                pady=14,
            )
            card.pack(side="left", fill="both", expand=True, padx=6)
            tk.Label(card, text=title, font=("Segoe UI", 10), fg="#72867f", bg="white").pack(
                anchor="w"
            )
            var = tk.StringVar(value="—")
            self.metric_vars[key] = var
            tk.Label(
                card, textvariable=var, font=("Segoe UI", 20, "bold"), fg="#147b63", bg="white"
            ).pack(anchor="w", pady=4)
            tk.Label(card, text=hint, font=("Segoe UI", 9), fg="#72867f", bg="white").pack(
                anchor="w"
            )

        self.budget_var = tk.StringVar(value="")
        tk.Label(
            page,
            textvariable=self.budget_var,
            font=("Segoe UI", 10, "bold"),
            fg="#203b38",
            bg="#f3f6f3",
        ).pack(anchor="w", pady=(8, 4))

        middle = tk.Frame(page, bg="#f3f6f3")
        middle.pack(fill="both", expand=True, pady=8)

        self.home_canvas = tk.Canvas(
            middle, bg="white", height=250, highlightbackground="#dce5df", highlightthickness=1
        )
        self.home_canvas.pack(side="left", fill="both", expand=True, padx=(0, 8))

        notif_card = tk.Frame(
            middle,
            bg="white",
            highlightbackground="#dce5df",
            highlightthickness=1,
            padx=14,
            pady=14,
            width=360,
        )
        notif_card.pack(side="right", fill="y")
        notif_card.pack_propagate(False)
        tk.Label(
            notif_card,
            text="Уведомления",
            font=("Segoe UI", 12, "bold"),
            fg="#203b38",
            bg="white",
        ).pack(anchor="w")
        self.notif_listbox = tk.Listbox(notif_card, font=("Segoe UI", 9), bd=1, relief="solid")
        self.notif_listbox.pack(fill="both", expand=True, pady=8)
        tk.Button(
            notif_card,
            text="Отметить прочитанными",
            font=("Segoe UI", 9, "bold"),
            bg="white",
            command=lambda: self.guard(self.mark_read),
        ).pack(fill="x")

        self.conn_var = tk.StringVar(value="")
        tk.Label(
            page, textvariable=self.conn_var, font=("Segoe UI", 9), fg="#72867f", bg="#f3f6f3"
        ).pack(anchor="w", pady=4)

        actions = tk.Frame(page, bg="#f3f6f3")
        actions.pack(fill="x", pady=4)
        for text, cmd in [
            ("Устройства и приборы →", lambda: self.navigate(Screen.DEVICES)),
            ("Посмотреть статистику →", lambda: self.navigate(Screen.REPORTS)),
            ("Помощник", self.voice_dialog),
        ]:
            tk.Button(
                actions,
                text=text,
                font=("Segoe UI", 10, "bold"),
                bg="white",
                padx=12,
                pady=6,
                command=lambda fn=cmd: self.guard(fn),
            ).pack(side="left", padx=4)

    def _build_devices(self) -> None:
        page = tk.Frame(self.container, bg="#f3f6f3", padx=28, pady=20)
        self.pages[Screen.DEVICES] = page

        tk.Label(
            page,
            text="Устройства и помещения",
            font=("Segoe UI", 18, "bold"),
            fg="#203b38",
            bg="#f3f6f3",
        ).pack(anchor="w")

        toolbar = tk.Frame(page, bg="#f3f6f3")
        toolbar.pack(fill="x", pady=8)
        self.device_toolbar_btns: list[tk.Button] = []
        for text, fn in [
            ("+ Добавить помещение", self.add_room),
            ("+ Добавить устройство", self.add_device),
            ("Импорт CSV", self.import_readings),
            ("Тест защиты", self.test_anomaly),
        ]:
            b = tk.Button(
                toolbar,
                text=text,
                font=("Segoe UI", 9, "bold"),
                bg="white",
                padx=10,
                pady=5,
                command=lambda f=fn: self.guard(f),
            )
            b.pack(side="left", padx=4)
            self.device_toolbar_btns.append(b)

        cols = ("name", "kind", "state", "power", "seen")
        self.device_tree = ttk.Treeview(page, columns=cols, show="headings", height=14)
        for col, heading in [
            ("name", "Комната / прибор"),
            ("kind", "Устройство учёта"),
            ("state", "Состояние"),
            ("power", "Мощность"),
            ("seen", "Последние данные"),
        ]:
            self.device_tree.heading(col, text=heading)
            self.device_tree.column(col, width=200)
        self.device_tree.pack(fill="both", expand=True, pady=8)

        tk.Button(
            page,
            text="← Назад в главное меню",
            font=("Segoe UI", 10, "bold"),
            bg="white",
            command=lambda: self.guard(lambda: self.navigate(Screen.HOME)),
        ).pack(anchor="w")

    def _build_reports(self) -> None:
        page = tk.Frame(self.container, bg="#f3f6f3", padx=28, pady=20)
        self.pages[Screen.REPORTS] = page

        top = tk.Frame(page, bg="#f3f6f3")
        top.pack(fill="x", pady=6)
        tk.Label(
            top,
            text="Статистика и аналитика",
            font=("Segoe UI", 18, "bold"),
            fg="#203b38",
            bg="#f3f6f3",
        ).pack(side="left")

        self.period_var = tk.StringVar(value="month")
        for text, val in [("День", "day"), ("Неделя", "week"), ("Месяц", "month")]:
            tk.Radiobutton(
                top,
                text=text,
                variable=self.period_var,
                value=val,
                bg="#f3f6f3",
                command=lambda: self.guard(self.refresh_reports),
            ).pack(side="left", padx=6)

        tk.Button(
            top,
            text="CSV ↓",
            bg="white",
            command=lambda: self.guard(lambda: self.export_report("csv")),
        ).pack(side="right", padx=4)
        tk.Button(
            top,
            text="PDF ↓",
            bg="white",
            command=lambda: self.guard(lambda: self.export_report("pdf")),
        ).pack(side="right", padx=4)

        self.report_summary_var = tk.StringVar(value="")
        tk.Label(
            page,
            textvariable=self.report_summary_var,
            font=("Segoe UI", 12, "bold"),
            fg="#203b38",
            bg="#f3f6f3",
        ).pack(anchor="w", pady=4)

        self.report_canvas = tk.Canvas(
            page, bg="white", height=200, highlightbackground="#dce5df", highlightthickness=1
        )
        self.report_canvas.pack(fill="x", pady=6)

        cols = ("name", "room", "kwh", "cost")
        self.report_tree = ttk.Treeview(page, columns=cols, show="headings", height=7)
        for col, heading in [
            ("name", "Прибор"),
            ("room", "Помещение"),
            ("kwh", "Расход, кВт·ч"),
            ("cost", "Стоимость, BYN"),
        ]:
            self.report_tree.heading(col, text=heading)
        self.report_tree.pack(fill="both", expand=True, pady=6)

        self.report_notes_var = tk.StringVar(value="")
        tk.Label(
            page,
            textvariable=self.report_notes_var,
            font=("Segoe UI", 9),
            fg="#203b38",
            bg="#f3f6f3",
            justify="left",
        ).pack(anchor="w", pady=4)

    def _build_settings(self) -> None:
        page = tk.Frame(self.container, bg="#f3f6f3", padx=28, pady=20)
        self.pages[Screen.SETTINGS] = page

        tk.Label(
            page,
            text="Настройки дома",
            font=("Segoe UI", 18, "bold"),
            fg="#203b38",
            bg="#f3f6f3",
        ).pack(anchor="w")

        card = tk.Frame(
            page, bg="white", highlightbackground="#dce5df", highlightthickness=1, padx=24, pady=20
        )
        card.pack(fill="both", expand=True, pady=12)

        self.budget_entry_var = tk.StringVar(value="100.0")
        self.daily_entry_var = tk.StringVar(value="25.0")
        self.day_rate_var = tk.StringVar(value="0.28")
        self.night_rate_var = tk.StringVar(value="0.14")
        self.day_start_var = tk.StringVar(value="7")
        self.night_start_var = tk.StringVar(value="23")
        self.notify_80_var = tk.BooleanVar(value=True)
        self.auto_protect_var = tk.BooleanVar(value=True)

        for label_text, var in [
            ("Месячный лимит, BYN", self.budget_entry_var),
            ("Дневной лимит, кВт·ч", self.daily_entry_var),
            ("День, BYN/кВт·ч", self.day_rate_var),
            ("Ночь, BYN/кВт·ч", self.night_rate_var),
            ("Начало дня, час (0–23)", self.day_start_var),
            ("Начало ночи, час (0–23)", self.night_start_var),
        ]:
            row = tk.Frame(card, bg="white")
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label_text, width=28, anchor="w", bg="white").pack(side="left")
            tk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)

        tk.Checkbutton(
            card,
            text="Уведомлять при достижении 80% месячного лимита",
            variable=self.notify_80_var,
            bg="white",
        ).pack(anchor="w", pady=4)
        tk.Checkbutton(
            card,
            text="Аварийно обесточивать прибор в модели при критической мощности",
            variable=self.auto_protect_var,
            bg="white",
        ).pack(anchor="w", pady=4)

        tk.Button(
            card,
            text="Сохранить и закрыть",
            font=("Segoe UI", 10, "bold"),
            bg="#187b63",
            fg="white",
            padx=16,
            pady=8,
            command=lambda: self.guard(self.save_settings),
        ).pack(anchor="e", pady=12)

    def _draw_bars(self, canvas: tk.Canvas, series: list[tuple[str, float]]) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 540)
        height = max(canvas.winfo_height(), 200)
        if not series:
            canvas.create_text(width // 2, height // 2, text="Нет данных", fill="#72867f")
            return
        max_val = max((v for _, v in series), default=1.0) or 1.0
        pad_x, pad_y = 40, 28
        avail_w = width - pad_x * 2
        avail_h = height - pad_y * 2
        step = avail_w / max(1, len(series))
        bar_w = max(8.0, step * 0.6)
        for idx, (lbl, val) in enumerate(series):
            x0 = pad_x + idx * step + (step - bar_w) / 2
            bar_h = (val / max_val) * (avail_h - 20)
            y0 = height - pad_y - bar_h
            y1 = height - pad_y
            canvas.create_rectangle(x0, y0, x0 + bar_w, y1, fill="#4b9b78", outline="#4b9b78")
            if len(series) <= 16 or idx % 3 == 0:
                canvas.create_text(
                    x0 + bar_w / 2,
                    height - 12,
                    text=lbl,
                    font=("Segoe UI", 8),
                    fill="#203b38",
                )

    def login(self) -> None:
        try:
            session = self.access.login(self.login_name_var.get(), self.login_pass_var.get())
            self.finish_login(session)
            self.login_error_var.set("")
        except DomainError as exc:
            self.login_error_var.set(str(exc))

    def finish_login(self, session) -> None:
        self.session = session
        self.user = self.access.require(session)
        role_title = "Владелец" if self.user["role"] == Role.OWNER else "Член семьи"
        self.profile_var.set(f"{self.user['name']} · {role_title}")
        if self.user["role"] == Role.OWNER:
            self.nav_buttons[Screen.SETTINGS].pack(side="left", padx=4, pady=12)
        else:
            self.nav_buttons[Screen.SETTINGS].pack_forget()
        self.header.pack(fill="x", before=self.container)
        self.navigate(Screen.HOME)

    def open_demo(self) -> None:
        seed_demo(self.energy)
        self.login_name_var.set("owner@demo.local")
        self.login_pass_var.set(DEMO_PASSWORD)
        self.login()
        if self.session:
            self.simulate_var.set(True)
            self.tick()

    def register(self) -> None:
        name = simpledialog.askstring("Регистрация", "Ваше имя:", parent=self.root)
        if not name:
            return
        login = simpledialog.askstring("Регистрация", "Логин / E-mail:", parent=self.root)
        password = simpledialog.askstring(
            "Регистрация", "Пароль (не менее 8 символов):", show="*", parent=self.root
        )
        home = simpledialog.askstring("Регистрация", "Название дома:", parent=self.root)
        if login and password and home:
            session = self.access.register_home(name, login, password, home)
            self.finish_login(session)

    def logout(self) -> None:
        if self.session:
            self.access.logout(self.session)
        self.session = None
        self.user = None
        self.simulate_var.set(False)
        self.login_pass_var.set("")
        if self.navigation.current != Screen.LOGIN:
            self.navigation.go(Screen.LOGIN)
        self.header.pack_forget()
        self._show_screen(Screen.LOGIN)

    def navigate(self, screen: Screen) -> None:
        self.user = self.access.require(self.session, owner=screen == Screen.SETTINGS)
        if screen != self.navigation.current:
            if (
                self.navigation.current not in (Screen.LOGIN, Screen.HOME)
                and screen != Screen.HOME
            ):
                self.navigation.go(Screen.HOME, Role(self.user["role"]))
            self.navigation.go(screen, Role(self.user["role"]))
        self._show_screen(screen)
        self.root.update_idletasks()
        {
            Screen.HOME: self.refresh_home,
            Screen.DEVICES: self.refresh_devices,
            Screen.REPORTS: self.refresh_reports,
            Screen.SETTINGS: self.refresh_settings,
        }[screen]()

    def refresh_home(self) -> None:
        if not self.session:
            return
        self.user = self.access.require(self.session)
        home = self.energy.home(self.session)
        self.home_title_var.set(f"Ваш дом сегодня · {home['name']}")
        devices = self.energy.devices(self.session)
        dashboard = self.reports.dashboard(self.session)
        power = sum(d["power_w"] for d in devices if d["counted"])
        self.metric_vars["power"].set(f"{power / 1000:.2f} кВт")
        self.metric_vars["daily"].set(f"{dashboard['daily_kwh']:.2f} кВт·ч")
        self.metric_vars["cost"].set(f"{dashboard['monthly_cost']:.2f} BYN")
        settings = self.energy.settings(self.session, owner=False)
        percent = dashboard["monthly_cost"] / settings["monthly_budget"] * 100
        self.budget_var.set(
            f"Бюджет дома: {dashboard['monthly_cost']:.2f} из "
            f"{settings['monthly_budget']:.2f} BYN · {percent:.0f}%"
        )
        week = self.reports.build(self.session, "week")
        self._draw_bars(self.home_canvas, week.series)
        self.notif_listbox.delete(0, "end")
        for n in self.energy.notifications.list(self.session, 30):
            dt = datetime.fromisoformat(n["created_at"]).astimezone(LOCAL_TZ)
            prefix = "● " if not n["is_read"] else ""
            self.notif_listbox.insert("end", f"{prefix}{dt:%d.%m %H:%M} {n['message']}")
        if self.notif_listbox.size() == 0:
            self.notif_listbox.insert("end", "Новых событий пока нет. Всё спокойно.")
        online = sum(d["online"] for d in devices)
        self.conn_var.set(
            f"На связи: {online} из {len(devices)} устройств · Обновлено {now():%H:%M:%S}"
        )

    def mark_read(self) -> None:
        self.energy.notifications.mark_read(self.session)
        self.refresh_home()

    def refresh_devices(self) -> None:
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)
        rooms = {r["id"]: r["name"] for r in self.energy.rooms(self.session)}
        kinds = {"meter": "Счётчик", "socket": "Умная розетка", "sensor": "Датчик"}
        for d in self.energy.devices(self.session):
            seen = d["last_seen"].strftime("%d.%m %H:%M:%S") if d["last_seen"] else "Нет показаний"
            self.device_tree.insert(
                "",
                "end",
                iid=str(d["id"]),
                values=(
                    f"{rooms.get(d['room_id'], '')} / {d['name']}",
                    kinds[d["kind"]],
                    STATE_LABELS[d["state"]],
                    f"{d['power_w']:.0f} Вт",
                    seen,
                ),
            )

    def add_room(self) -> None:
        name = simpledialog.askstring("Новое помещение", "Название комнаты:", parent=self.root)
        if name:
            self.energy.add_room(self.session, name)
            self.refresh_devices()

    def add_device(self) -> None:
        rooms = self.energy.rooms(self.session)
        if not rooms:
            raise ValidationError("Сначала добавьте помещение.")
        name = simpledialog.askstring("Новое устройство", "Название прибора:", parent=self.root)
        serial = simpledialog.askstring("Новое устройство", "Серийный номер:", parent=self.root)
        if name and serial:
            self.energy.add_device(
                self.session, rooms[0]["id"], name, serial, "socket", 1000, 3000
            )
            self.refresh_devices()

    def import_readings(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Импорт показаний", filetypes=[("CSV", "*.csv")]
        )
        if path:
            count, errors = import_csv(self.energy, self.session, path)
            self.refresh_devices()
            messagebox.showinfo(
                "Результат импорта",
                f"Принято: {count}. Ошибок: {len(errors)}.",
                parent=self.root,
            )

    def test_anomaly(self) -> None:
        sel = self.device_tree.selection()
        if not sel:
            raise ValidationError("Выберите демонстрационный прибор в списке.")
        device = self.energy.device(self.session, int(sel[0]))
        if not device["serial"].startswith("DEMO-"):
            raise ValidationError("Проверка доступна только демонстрационным приборам.")
        start = now() - timedelta(seconds=1)
        power = device["critical_w"] * 1.5
        self.energy.ingest(
            self.session, ReadingPacket(device["id"], start, power, power / 3_600_000, 1)
        )
        self.refresh_devices()

    def refresh_reports(self) -> None:
        report = self.reports.build(self.session, self.period_var.get())
        self.current_report = report
        change = (
            f"{report.change_percent:+.1f}%" if report.change_percent is not None else "нет данных"
        )
        self.report_summary_var.set(
            f"{report.energy_kwh:.2f} кВт·ч · {report.cost:.2f} BYN · "
            f"К предыдущему периоду: {change}"
        )
        self._draw_bars(self.report_canvas, report.series)
        for item in self.report_tree.get_children():
            self.report_tree.delete(item)
        for d in report.devices:
            self.report_tree.insert(
                "",
                "end",
                values=(d["name"], d["room"], f"{d['energy_kwh']:.3f}", f"{d['cost']:.2f}"),
            )
        self.report_notes_var.set("\n".join(f"• {r}" for r in report.recommendations))

    def export_report(self, kind: str) -> None:
        self.refresh_reports()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Сохранить отчёт",
            initialfile=f"energy-report.{kind}",
            defaultextension=f".{kind}",
        )
        if not path:
            return
        target = Path(path)
        if kind == "csv":
            self.reports.export_csv(self.session, self.current_report, target)
        else:
            self.reports.export_pdf(self.session, self.current_report, target)
        messagebox.showinfo("Экспорт", f"Отчёт сохранён: {target}", parent=self.root)

    def refresh_settings(self) -> None:
        s = self.energy.settings(self.session)
        self.budget_entry_var.set(str(s["monthly_budget"]))
        self.daily_entry_var.set(str(s["daily_kwh"]))
        self.day_rate_var.set(str(s["day_rate"]))
        self.night_rate_var.set(str(s["night_rate"]))
        self.day_start_var.set(str(s["day_start"]))
        self.night_start_var.set(str(s["night_start"]))
        self.notify_80_var.set(bool(s["notify_80"]))
        self.auto_protect_var.set(bool(s["auto_protect"]))

    def save_settings(self) -> None:
        self.energy.save_settings(
            self.session,
            monthly_budget=float(self.budget_entry_var.get()),
            daily_kwh=float(self.daily_entry_var.get()),
            day_rate=float(self.day_rate_var.get()),
            night_rate=float(self.night_rate_var.get()),
            day_start=int(self.day_start_var.get()),
            night_start=int(self.night_start_var.get()),
            notify_80=self.notify_80_var.get(),
            auto_protect=self.auto_protect_var.get(),
        )
        self.navigate(Screen.HOME)

    def voice_dialog(self) -> None:
        cmd = simpledialog.askstring(
            "Помощник",
            "Введите команду (текущее потребление; отчёт за месяц; "
            "эко-режим; отпуск; ночной тариф):",
            parent=self.root,
        )
        if cmd:
            reply = self.assistant.execute(self.session, self.assistant.recognize(cmd))
            messagebox.showinfo("Помощник", reply, parent=self.root)

    def tick(self) -> None:
        if self.session:
            self.user = self.access.require(self.session)
            if self.user["role"] == Role.OWNER:
                self.automation.tick(self.session)
                if self.simulate_var.get():
                    self.simulator.tick(self.session)
                self.gateway.drain()
            if self.navigation.current == Screen.HOME:
                self.refresh_home()
            elif self.navigation.current == Screen.DEVICES:
                self.refresh_devices()

    def _schedule_tick(self) -> None:
        self.guard(self.tick)
        self.root.after(10_000, self._schedule_tick)

    def run(self) -> int:
        self.root.mainloop()
        return 0
