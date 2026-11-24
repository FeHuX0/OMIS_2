/* Client-side logic for the 5 Energy Home screens (FSM 5.2 & Mockups 5.3) */

let sessionToken = null;
let appState = null;
let currentPeriod = "week";
const collapsedRooms = new Map();
let modalSubmitHandler = null;

const SCREEN_MAP = {
  auth: "screen-login",
  main: "screen-home",
  devices: "screen-devices",
  reports: "screen-reports",
  settings: "screen-settings",
};

async function apiRequest(method, url, payload) {
  const headers = {};
  if (sessionToken) {
    headers["Authorization"] = `Bearer ${sessionToken}`;
  }
  const opts = { method, headers };
  if (payload !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(payload);
  }
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || "Ошибка запроса.");
  }
  return data;
}

function showScreen(screenName) {
  Object.entries(SCREEN_MAP).forEach(([fsmName, domId]) => {
    const el = document.getElementById(domId);
    if (el) {
      el.classList.toggle("hidden", fsmName !== screenName);
    }
  });
}

function formatNumber(value, decimals) {
  return Number(value).toFixed(decimals);
}

function renderAll() {
  if (!appState) {
    showScreen("auth");
    return;
  }
  showScreen(appState.current_window || "main");

  const user = appState.user;
  const main = appState.main;

  // Screen 2: Main window
  document.getElementById("home-power").textContent = `${formatNumber(main.current_power_kw, 1)} кВт`;
  document.getElementById("home-daily").textContent = `${formatNumber(main.daily_consumption_kwh, 1)} кВт/ч`;
  const alertBox = document.getElementById("home-alert-box");
  if (main.alerts && main.alerts.length > 0) {
    alertBox.textContent = main.alerts[0];
  } else {
    alertBox.textContent = "Потребление энергии в пределах установленного лимита.";
  }
  document.getElementById("home-profile").textContent = `Профиль: ${user.role_label}`;
  document.getElementById("home-notice").textContent = "";

  // Screen 3: Devices window
  renderRoomsTree(appState.rooms || [], user.is_owner);
  const addDevBtn = document.getElementById("btn-add-device");
  const addRoomBtn = document.getElementById("btn-add-room");
  addDevBtn.disabled = !user.is_owner;
  addRoomBtn.disabled = !user.is_owner;

  // Screen 4: Reports window
  renderReport(appState.report);

  // Screen 5: Settings window
  const settings = appState.settings;
  if (appState.current_window !== "settings" || document.activeElement.tagName !== "INPUT") {
    document.getElementById("set-monthly-limit").value = String(Math.round(settings.monthly_budget_byn));
    document.getElementById("set-day-rate").value = formatNumber(settings.day_rate, 2);
    document.getElementById("set-night-rate").value = formatNumber(settings.night_rate, 2);
    document.getElementById("set-notify-80").checked = Boolean(settings.notify_at_80);
  }
}

function renderRoomsTree(rooms, isOwner) {
  const container = document.getElementById("rooms-tree");
  container.textContent = "";

  rooms.forEach((room) => {
    if (!collapsedRooms.has(room.id)) {
      collapsedRooms.set(room.id, Boolean(room.collapsed_by_default));
    }
    const isCollapsed = collapsedRooms.get(room.id);

    const node = document.createElement("div");
    node.className = "room-node";
    node.setAttribute("role", "treeitem");
    node.setAttribute("aria-expanded", String(!isCollapsed));

    const headerBtn = document.createElement("button");
    headerBtn.type = "button";
    headerBtn.className = "room-header-btn";
    headerBtn.setAttribute("aria-expanded", String(!isCollapsed));

    const toggleBox = document.createElement("span");
    toggleBox.className = "tree-toggle-box";
    toggleBox.setAttribute("aria-hidden", "true");
    toggleBox.textContent = isCollapsed ? "+" : "−";

    const titleSpan = document.createElement("span");
    titleSpan.textContent = room.name;

    headerBtn.appendChild(toggleBox);
    headerBtn.appendChild(titleSpan);
    headerBtn.addEventListener("click", () => {
      collapsedRooms.set(room.id, !collapsedRooms.get(room.id));
      renderRoomsTree(appState.rooms || [], appState.user.is_owner);
    });
    node.appendChild(headerBtn);

    if (!isCollapsed) {
      const list = document.createElement("ul");
      list.className = "room-devices-list";

      if (!room.appliances || room.appliances.length === 0) {
        const emptyLi = document.createElement("li");
        emptyLi.className = "device-empty-note";
        emptyLi.textContent = "• Нет подключённых устройств";
        list.appendChild(emptyLi);
      } else {
        room.appliances.forEach((dev) => {
          const li = document.createElement("li");
          const itemBtn = document.createElement("button");
          itemBtn.type = "button";
          itemBtn.className = "device-item-btn";
          itemBtn.textContent = `• ${dev.tree_label}`;
          itemBtn.addEventListener("click", () => openDeviceControlModal(room, dev, isOwner));
          li.appendChild(itemBtn);
          list.appendChild(li);
        });
      }
      node.appendChild(list);
    }

    container.appendChild(node);
  });
}

function renderReport(report) {
  if (!report) return;
  document.querySelectorAll(".period-check").forEach((cb) => {
    cb.checked = cb.dataset.period === report.period;
  });

  const chart = report.chart;
  document.getElementById("report-chart-title").textContent = chart.title;
  document.getElementById("report-chart-subtitle").textContent = chart.subtitle;
  drawReportChart(chart);

  const anomContainer = document.getElementById("report-anomalies-list");
  anomContainer.textContent = "";
  if (!report.anomalies || report.anomalies.length === 0) {
    const p = document.createElement("p");
    p.textContent = "Аномалии не выявлены";
    anomContainer.appendChild(p);
  } else {
    report.anomalies.forEach((a) => {
      const p = document.createElement("p");
      p.textContent = a.description;
      anomContainer.appendChild(p);
    });
  }
}

function drawReportChart(chart) {
  const svg = document.getElementById("report-chart-svg");
  const svgNs = "http://www.w3.org/2000/svg";
  svg.textContent = "";

  const W = 320;
  const H = 185;
  const padL = 34;
  const padR = 20;
  const padT = 16;
  const padB = 26;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const x0 = padL;
  const y0 = H - padB;

  const maxY = Math.max(chart.y_max || 25, ...chart.values, 1);
  const stepY = chart.y_step || 5;

  const addEl = (tag, attrs, text) => {
    const el = document.createElementNS(svgNs, tag);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
    if (text !== undefined) el.textContent = String(text);
    svg.appendChild(el);
    return el;
  };

  // Y axis with upward arrow
  addEl("line", { x1: x0, y1: y0, x2: x0, y2: padT - 4, stroke: "#111111", "stroke-width": 1.3 });
  addEl("polygon", {
    points: `${x0},${padT - 8} ${x0 - 3.5},${padT - 1} ${x0 + 3.5},${padT - 1}`,
    fill: "#111111",
  });

  // X axis with rightward arrow
  addEl("line", { x1: x0, y1: y0, x2: W - padR + 8, y2: y0, stroke: "#111111", "stroke-width": 1.3 });
  addEl("polygon", {
    points: `${W - padR + 12},${y0} ${W - padR + 5},${y0 - 3.5} ${W - padR + 5},${y0 + 3.5}`,
    fill: "#111111",
  });

  // Y axis ticks
  for (let val = 0; val < maxY; val += stepY) {
    const y = y0 - (val / maxY) * plotH;
    addEl(
      "text",
      {
        x: x0 - 6,
        y: y + 4,
        "text-anchor": "end",
        "font-size": 12,
        fill: "#111111",
        "font-family": "Arial, sans-serif",
      },
      Number.isInteger(val) ? String(val) : val.toFixed(1)
    );
  }

  // Flat gray bars with white values near the bottom inside each bar
  const count = chart.labels.length || 1;
  const slotW = plotW / count;
  const barW = Math.min(30, Math.max(14, slotW * 0.72));

  chart.labels.forEach((label, idx) => {
    const val = Number(chart.values[idx] || 0);
    const barH = Math.max(0, (val / maxY) * plotH);
    const bx = x0 + idx * slotW + (slotW - barW) / 2 + 2;
    const by = y0 - barH;

    if (val > 0) {
      addEl("rect", {
        x: bx.toFixed(1),
        y: by.toFixed(1),
        width: barW.toFixed(1),
        height: barH.toFixed(1),
        fill: "#b3b3b3",
      });
      if (barH >= 14) {
        addEl(
          "text",
          {
            x: (bx + barW / 2).toFixed(1),
            y: (y0 - 5).toFixed(1),
            "text-anchor": "middle",
            "font-size": 9,
            fill: "#ffffff",
            "font-family": "Arial, sans-serif",
          },
          val.toFixed(1)
        );
      }
    }

    addEl(
      "text",
      {
        x: (bx + barW / 2).toFixed(1),
        y: y0 + 16,
        "text-anchor": "middle",
        "font-size": 11,
        fill: "#111111",
        "font-family": "Arial, sans-serif",
      },
      label
    );
  });
}

async function navigateTo(targetScreen) {
  const homeNotice = document.getElementById("home-notice");
  if (homeNotice) homeNotice.textContent = "";
  try {
    appState = await apiRequest("POST", "/api/navigate", {
      target: targetScreen,
      period: currentPeriod,
    });
    renderAll();
  } catch (err) {
    if (homeNotice && appState && appState.current_window === "main") {
      homeNotice.textContent = err.message;
    }
  }
}

async function refreshState() {
  if (!sessionToken) return;
  appState = await apiRequest("GET", `/api/state?period=${encodeURIComponent(currentPeriod)}`);
  renderAll();
}

// ---------------------------------------------------------------------------
// Monochrome modals (registration, add room/device, appliance FSM 5.1)
// ---------------------------------------------------------------------------

function openWireModal({ title, submitLabel, hideSubmit, buildFields, onSubmit }) {
  const dialog = document.getElementById("wire-modal");
  document.getElementById("wire-modal-title").textContent = title;
  const fieldsBox = document.getElementById("wire-modal-fields");
  fieldsBox.textContent = "";
  document.getElementById("wire-modal-error").textContent = "";
  const submitBtn = document.getElementById("wire-modal-submit");
  submitBtn.textContent = submitLabel || "Сохранить";
  submitBtn.classList.toggle("hidden", Boolean(hideSubmit));

  buildFields(fieldsBox);
  modalSubmitHandler = onSubmit || null;
  dialog.showModal();
}

function closeWireModal() {
  const dialog = document.getElementById("wire-modal");
  modalSubmitHandler = null;
  if (dialog.open) dialog.close();
}

function createFieldGroup(labelText, inputEl) {
  const wrap = document.createElement("div");
  wrap.className = "field-group";
  const label = document.createElement("label");
  label.className = "field-label";
  label.htmlFor = inputEl.id;
  label.textContent = labelText;
  wrap.appendChild(label);
  wrap.appendChild(inputEl);
  return wrap;
}

function openRegisterModal() {
  openWireModal({
    title: "Регистрация пользователя",
    submitLabel: "Зарегистрироваться",
    buildFields: (box) => {
      const loginIn = document.createElement("input");
      loginIn.id = "reg-login";
      loginIn.type = "text";
      loginIn.className = "wire-input";
      loginIn.placeholder = "new@example.com";

      const nameIn = document.createElement("input");
      nameIn.id = "reg-name";
      nameIn.type = "text";
      nameIn.className = "wire-input";
      nameIn.placeholder = "Имя";

      const passIn = document.createElement("input");
      passIn.id = "reg-password";
      passIn.type = "password";
      passIn.className = "wire-input";
      passIn.placeholder = "Не менее 6 символов";

      const roleSel = document.createElement("select");
      roleSel.id = "reg-role";
      roleSel.className = "wire-select";
      [
        ["OWNER", "Владелец дома"],
        ["FAMILY_MEMBER", "Член семьи"],
      ].forEach(([val, text]) => {
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = text;
        roleSel.appendChild(opt);
      });

      box.appendChild(createFieldGroup("Login / E-mail:", loginIn));
      box.appendChild(createFieldGroup("Имя:", nameIn));
      box.appendChild(createFieldGroup("Пароль:", passIn));
      box.appendChild(createFieldGroup("Роль:", roleSel));
    },
    onSubmit: async () => {
      const res = await apiRequest("POST", "/api/register", {
        login: document.getElementById("reg-login").value,
        name: document.getElementById("reg-name").value,
        password: document.getElementById("reg-password").value,
        role: document.getElementById("reg-role").value,
      });
      sessionToken = res.token;
      appState = res.state;
      collapsedRooms.clear();
      closeWireModal();
      renderAll();
    },
  });
}

function openAddRoomModal() {
  openWireModal({
    title: "Новое помещение",
    submitLabel: "Добавить помещение",
    buildFields: (box) => {
      const nameIn = document.createElement("input");
      nameIn.id = "room-name-input";
      nameIn.type = "text";
      nameIn.className = "wire-input";
      nameIn.placeholder = "Например: Прихожая";
      box.appendChild(createFieldGroup("Название помещения:", nameIn));
    },
    onSubmit: async () => {
      const res = await apiRequest("POST", "/api/rooms", {
        name: document.getElementById("room-name-input").value,
      });
      appState = res.state;
      closeWireModal();
      renderAll();
    },
  });
}

function openAddDeviceModal() {
  const rooms = (appState && appState.rooms) || [];
  openWireModal({
    title: "Новое устройство",
    submitLabel: "Добавить устройство",
    buildFields: (box) => {
      const roomSel = document.createElement("select");
      roomSel.id = "dev-room-select";
      roomSel.className = "wire-select";
      rooms.forEach((r) => {
        const opt = document.createElement("option");
        opt.value = String(r.id);
        opt.textContent = r.name;
        roomSel.appendChild(opt);
      });

      const nameIn = document.createElement("input");
      nameIn.id = "dev-name-input";
      nameIn.type = "text";
      nameIn.className = "wire-input";
      nameIn.placeholder = "Например: Обогреватель";

      const typeSel = document.createElement("select");
      typeSel.id = "dev-type-select";
      typeSel.className = "wire-select";
      ["Умная розетка", "Счётчик", "Датчик"].forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = t;
        typeSel.appendChild(opt);
      });

      const serialIn = document.createElement("input");
      serialIn.id = "dev-serial-input";
      serialIn.type = "text";
      serialIn.className = "wire-input";
      serialIn.value = `DEV-${Date.now().toString().slice(-4)}`;

      const powerIn = document.createElement("input");
      powerIn.id = "dev-power-input";
      powerIn.type = "number";
      powerIn.min = "0";
      powerIn.step = "10";
      powerIn.className = "wire-input";
      powerIn.value = "500";

      box.appendChild(createFieldGroup("Помещение:", roomSel));
      box.appendChild(createFieldGroup("Название прибора / устройства:", nameIn));
      box.appendChild(createFieldGroup("Тип устройства учёта:", typeSel));
      box.appendChild(createFieldGroup("Серийный номер:", serialIn));
      box.appendChild(createFieldGroup("Мощность (Вт):", powerIn));
    },
    onSubmit: async () => {
      const res = await apiRequest("POST", "/api/devices", {
        room_id: Number(document.getElementById("dev-room-select").value),
        name: document.getElementById("dev-name-input").value,
        device_type: document.getElementById("dev-type-select").value,
        serial: document.getElementById("dev-serial-input").value,
        power_w: Number(document.getElementById("dev-power-input").value),
      });
      appState = res.state;
      closeWireModal();
      renderAll();
    },
  });
}

function openDeviceControlModal(room, dev, isOwner) {
  openWireModal({
    title: dev.name,
    submitLabel: "Применить состояние",
    hideSubmit: !isOwner || !dev.available_actions || dev.available_actions.length === 0,
    buildFields: (box) => {
      const info = document.createElement("p");
      info.className = "wire-modal-info";
      const sub = dev.inner_state ? ` / ${dev.inner_state}` : "";
      info.textContent =
        `Помещение: ${room.name}\n` +
        `Устройство учёта: ${dev.device_type} (${dev.serial_number})\n` +
        `Состояние (5.1): ${dev.outer_state}${sub}\n` +
        `Текущая мощность: ${Math.round(dev.current_power_w)} Вт`;
      info.style.whiteSpace = "pre-line";
      box.appendChild(info);

      if (isOwner && dev.available_actions && dev.available_actions.length > 0) {
        const actSel = document.createElement("select");
        actSel.id = "dev-fsm-action";
        actSel.className = "wire-select";
        dev.available_actions.forEach((a) => {
          const opt = document.createElement("option");
          opt.value = a.id;
          opt.textContent = a.label;
          actSel.appendChild(opt);
        });
        box.appendChild(createFieldGroup("Переход состояния прибора (5.1):", actSel));
      }
    },
    onSubmit: async () => {
      const action = document.getElementById("dev-fsm-action").value;
      const res = await apiRequest("POST", "/api/device-state", {
        device_id: dev.id,
        action,
      });
      appState = res.state;
      closeWireModal();
      renderAll();
    },
  });
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("login-error");
  errEl.textContent = "";
  try {
    const res = await apiRequest("POST", "/api/login", {
      login: document.getElementById("login-input").value,
      password: document.getElementById("password-input").value,
    });
    sessionToken = res.token;
    appState = res.state;
    collapsedRooms.clear();
    renderAll();
  } catch (err) {
    errEl.textContent = err.message;
  }
});

document.getElementById("btn-open-register").addEventListener("click", openRegisterModal);

document.getElementById("btn-go-devices").addEventListener("click", () => navigateTo("devices"));
document.getElementById("btn-go-reports").addEventListener("click", () => navigateTo("reports"));
document.getElementById("btn-go-settings").addEventListener("click", () => navigateTo("settings"));

document.getElementById("btn-logout").addEventListener("click", async () => {
  try {
    await apiRequest("POST", "/api/logout", {});
  } catch (_) {}
  sessionToken = null;
  appState = null;
  collapsedRooms.clear();
  document.getElementById("login-error").textContent = "";
  showScreen("auth");
});

document.getElementById("btn-devices-back").addEventListener("click", () => navigateTo("main"));
document.getElementById("btn-add-room").addEventListener("click", openAddRoomModal);
document.getElementById("btn-add-device").addEventListener("click", openAddDeviceModal);

document.getElementById("btn-reports-back").addEventListener("click", () => navigateTo("main"));
document.querySelectorAll(".period-check").forEach((cb) => {
  cb.addEventListener("change", async () => {
    currentPeriod = cb.dataset.period || "week";
    await refreshState();
  });
});

document.getElementById("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("settings-error");
  errEl.textContent = "";
  try {
    const res = await apiRequest("POST", "/api/settings", {
      monthly_limit: Number(document.getElementById("set-monthly-limit").value),
      day_rate: Number(document.getElementById("set-day-rate").value),
      night_rate: Number(document.getElementById("set-night-rate").value),
      notify_80: document.getElementById("set-notify-80").checked,
    });
    appState = res.state;
    renderAll();
  } catch (err) {
    errEl.textContent = err.message;
  }
});

document.getElementById("btn-cancel-settings").addEventListener("click", async () => {
  document.getElementById("settings-error").textContent = "";
  await navigateTo("main");
});

document.getElementById("wire-modal-cancel").addEventListener("click", closeWireModal);
document.getElementById("wire-modal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("wire-modal-error");
  errEl.textContent = "";
  if (!modalSubmitHandler) {
    closeWireModal();
    return;
  }
  try {
    await modalSubmitHandler();
  } catch (err) {
    errEl.textContent = err.message;
  }
});
