"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  config: null,
  revision: null,
  status: {},
  report: null,
  expenses: [],
  page: "overview",
  csrf: null,
  authRequired: true,
  discovery: null,
};
const categories = [
  "Electrical",
  "Heating hot water",
  "Chilled water",
  "Domestic hot water",
  "Sauna",
  "Cafeteria",
  "Pool / hot tub",
  "Cooling towers",
  "Other",
];
const units = [
  "kWh",
  "kWh-thermal",
  "gal",
  "m3",
  "L",
  "therm",
  "kBtu",
  "MMBtu",
  "kg",
  "lb",
  "count",
];
const escape = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (n, d = 1) =>
  Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: d });
const cash = (n) =>
  Number(n || 0).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });
const uid = (prefix) =>
  prefix +
  "-" +
  (crypto.randomUUID
    ? crypto.randomUUID().slice(0, 12)
    : Math.random().toString(36).slice(2, 14));
const option = (v, label, selected) =>
  `<option value="${escape(v)}" ${v === selected ? "selected" : ""}>${escape(label ?? v)}</option>`;
const dateStr = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const empty = (title, detail, icon = "◫") =>
  `<div class="empty"><span class="empty-icon">${icon}</span><strong>${escape(title)}</strong>${escape(detail)}</div>`;
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = error ? "error" : "";
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(
    () => ($("toast").hidden = true),
    error ? 10000 : 4500,
  );
}
async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(state.csrf ? { "X-CSRF-Token": state.csrf } : {}),
    ...options.headers,
  };
  const response = await fetch(path, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error("The server returned an unreadable response");
  }
  if (!response.ok) {
    if (response.status === 401 && path != "/api/login") showLogin();
    const detail = data.detail;
    throw Error(
      Array.isArray(detail)
        ? detail.map((e) => e.loc.slice(1).join(".") + ": " + e.msg).join("; ")
        : detail || "Request failed",
    );
  }
  return data;
}
async function action(button, fn) {
  if (button) button.disabled = true;
  try {
    return await fn();
  } catch (e) {
    toast(e.message, true);
    throw e;
  } finally {
    if (button) button.disabled = false;
  }
}
function bindAction(id, fn) {
  $(id).addEventListener("click", (e) => {
    e.preventDefault();
    action(e.currentTarget, fn).catch(() => {});
  });
}
function showLogin() {
  $("app-view").hidden = true;
  $("login-view").hidden = false;
  state.csrf = null;
}
function useSession(session) {
  state.csrf = session.csrf;
  state.authRequired = session.auth_required !== false;
  $("logout").hidden = !state.authRequired;
  $("password-panel").hidden = !state.authRequired;
  $("auth-disabled").hidden = state.authRequired;
}
async function load() {
  const [cfg, status, expenses] = await Promise.all([
    api("/api/config"),
    api("/api/status"),
    api("/api/expenses"),
  ]);
  state.config = cfg.configuration;
  state.revision = cfg.revision;
  state.status = status;
  state.expenses = expenses;
  $("app-view").hidden = false;
  $("login-view").hidden = true;
  document.querySelector(".timezone").textContent = state.config.timezone;
  renderMeters();
  renderSources();
  bindSqlSourceActions();
  renderRates();
  renderSettings();
  renderExpenses();
  renderMonthlyPlan();
  await runReport();
  renderExpenses();
  renderMeters();
}
async function saveConfig(config) {
  const result = await api("/api/config", {
    method: "PUT",
    body: { configuration: config, revision: state.revision },
  });
  state.config = result.configuration;
  state.revision = result.revision;
  return result;
}
function copyConfig() {
  return structuredClone(state.config);
}
const pages = {
  overview: [
    "THE BIG PICTURE",
    "Utility overview",
    "One workspace for every meter, system and shared cost.",
  ],
  monthly: [
    "MONTH BY MONTH",
    "Monthly billing",
    "Historical usage, the right rate schedule and a bill for each month.",
  ],
  meters: [
    "YOUR MEASUREMENT NETWORK",
    "Meter inventory",
    "Track inputs, rename meters and see the quality behind every reading.",
  ],
  canvas: [
    "MAKE THE CONNECTIONS",
    "Calculation canvas",
    "Build a clear picture of how your utility systems add up.",
  ],
  rates: [
    "TIME-OF-USE PRICING",
    "PG&E rates",
    "The right service details. The latest published rates.",
  ],
  expenses: [
    "TRANSPARENT COST SHARING",
    "Bills & allocation",
    "Bring every utility expense into the accounting picture.",
  ],
  discovery: [
    "CONNECT YOUR NETWORK",
    "Sources & discovery",
    "Find the meters that power your workspace.",
  ],
  settings: [
    "YOUR WORKSPACE",
    "Settings",
    "Configure collection, billing preferences and access.",
  ],
};
function navigate(page) {
  if (!pages[page]) page = "overview";
  state.page = page;
  document
    .querySelectorAll(".page")
    .forEach((p) => (p.hidden = p.id !== "page-" + page));
  document
    .querySelectorAll(".nav")
    .forEach((n) => n.classList.toggle("active", n.dataset.page === page));
  const info = pages[page];
  $("page-eyebrow").textContent = info[0];
  $("page-title").textContent = info[1];
  $("page-description").textContent = info[2];
  $("breadcrumb").innerHTML = `Workspace <span>/ ${escape(info[1])}</span>`;
  if (page === "canvas") renderCanvas();
  if (page === "settings") loadAudit();
  if (page === "monthly") loadMonthlyImportStatus();
  history.replaceState(null, "", "#" + page);
}
function meterStatus(m) {
  const latest = state.status.latest?.[m.id];
  const collector = state.status.collector?.[m.id];
  if (m.mode === "virtual") return { label: "Calculated", type: "purple" };
  if (!m.enabled) return { label: "Disabled", type: "warning" };
  if (collector?.quality === "offline")
    return { label: "Offline", type: "error" };
  if (!latest) return { label: "Awaiting data", type: "warning" };
  if (Date.now() / 1000 - latest.timestamp > m.max_gap_seconds)
    return { label: "Stale", type: "warning" };
  return {
    label: latest.quality === "good" ? "Collecting" : latest.quality,
    type: latest.quality === "good" ? "" : "warning",
  };
}
async function runReport() {
  if (!$("report-start").value || !$("report-end").value) return;
  const query = new URLSearchParams({
    start: $("report-start").value,
    end: $("report-end").value,
  });
  state.report = await api("/api/report?" + query);
  $("export-report").href = "/api/report.csv?" + query;
  renderOverview();
  if (state.page === "canvas") renderCanvas();
}
function renderOverview() {
  const r = state.report;
  if (!r) return;
  const physical = state.config.meters.filter((m) => m.mode !== "virtual");
  const good = physical.filter(
    (m) => meterStatus(m).label === "Collecting",
  ).length;
  const allocated = r.allocations.filter((a) => a.allocated).length;
  $("overview-stats").innerHTML = [
    [
      "Period total",
      cash(r.total_cost),
      "Includes " + cash(r.markup) + " in markup",
      "↗",
    ],
    [
      "Connected meters",
      `${physical.length}<small>inputs</small>`,
      `${good} collecting · ${state.config.meters.length - physical.length} calculated`,
      "◉",
    ],
    [
      "Shared bills",
      `${state.expenses.length}<small>recorded</small>`,
      `${allocated} allocated in this period`,
      "▤",
    ],
    [
      "Rate schedule",
      escape(state.config.rate.schedule || "Not set"),
      state.status.tariffs?.length
        ? "Published tariff imported"
        : "Choose service details to price energy",
      "◷",
    ],
  ]
    .map(
      ([title, value, foot, icon]) =>
        `<div class="stat"><div class="stat-label">${title}<span class="stat-icon">${icon}</span></div><div class="stat-value">${value}</div><div class="stat-foot">${escape(foot)}</div></div>`,
    )
    .join("");
  const last = state.status.last_poll;
  $("last-updated").textContent = last
    ? "Last read " +
      new Date(last * 1000).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Waiting for meter data";
  const systems = {};
  r.meters.forEach(
    (m) => (systems[m.category] = (systems[m.category] || 0) + m.total_cost),
  );
  const colors = [
    "#79976b",
    "#a4b985",
    "#c3cfaa",
    "#b39bc7",
    "#d4b790",
    "#a9b6b4",
  ];
  $("system-breakdown").innerHTML =
    Object.entries(systems)
      .filter(([c, n]) => n > 0)
      .sort((a, b) => b[1] - a[1])
      .map(
        ([cat, n], i) =>
          `<div class="system-row"><div class="system-row-title"><span>${escape(cat)}</span><strong>${cash(n)}</strong></div><div class="bar-track"><div class="bar-fill" style="width:${r.total_cost > 0 ? (n / r.total_cost) * 100 : 0}%;background:${colors[i % colors.length]}"></div></div></div>`,
      )
      .join("") ||
    empty(
      "Costs will appear here",
      "Connect meters and configure rates or shared bills.",
      "◷",
    );
  if (r.total_cost)
    $("system-breakdown").innerHTML +=
      `<div class="system-total"><span>Period total</span><strong>${cash(r.total_cost)}</strong></div>`;
  $("report-rows").innerHTML =
    r.meters
      .map(
        (m) =>
          `<tr><td>${escape(m.name)}<small>${escape(m.category)}</small></td><td>${fmt(m.quantity)} <small>${escape(m.unit)}</small></td><td>${fmt(m.coverage * 100, 1)}%</td><td>${cash(m.base_cost)}</td><td>${cash(m.markup)}<small>${m.markup_percent}%</small></td><td>${cash(m.total_cost)}</td><td><span class="tag ${m.issues.length ? "warning" : ""}" title="${escape(m.issues.join("; "))}">${m.issues.length ? "Review data" : "Complete"}</span></td></tr>`,
      )
      .join("") ||
    `<tr><td colspan="7">${empty("A place for every meter", "Start in Sources & discovery, or add a meter manually.")}</td></tr>`;
  const notes = [
    ...r.notes,
    ...r.meters.flatMap((m) =>
      (m.electricity?.notes || []).map((n) => m.name + ": " + n),
    ),
    ...r.allocations
      .filter((a) => !a.allocated)
      .map((a) => a.description + ": " + a.issues.join("; ")),
  ];
  $("report-notes").innerHTML = notes.length
    ? `<details class="info-strip"><summary>${r.complete ? "Billing notes" : "Provisional report"} · ${notes.length} item${notes.length === 1 ? "" : "s"} to review</summary><p>${notes.map(escape).join("<br>")}</p></details>`
    : !r.complete && r.meters.length
      ? '<div class="info-strip">Collection is in progress. Full interval coverage is required for complete billing.</div>'
      : "";
  const selected = $("chart-meter").value;
  $("chart-meter").innerHTML = state.config.meters
    .filter((m) => m.mode !== "virtual")
    .map((m) => option(m.id, m.name, selected))
    .join("");
  drawChart().catch((e) => toast(e.message, true));
}
async function drawChart() {
  const mid = $("chart-meter").value;
  if (!mid) {
    $("usage-chart").innerHTML = empty(
      "Your data, in view",
      "Connect a meter to see its recorded readings.",
      "⌁",
    );
    return;
  }
  const m = state.config.meters.find((m) => m.id === mid);
  const query = new URLSearchParams({
    start: $("report-start").value,
    end: $("report-end").value,
  });
  const d = await api("/api/history/" + encodeURIComponent(mid) + "?" + query);
  if ($("chart-meter").value !== mid) return;
  const readings = d.readings.filter((r) => r.quality === "good");
  if (readings.length < 2) {
    $("usage-chart").innerHTML = empty(
      "Collection is starting",
      "At least two verified readings are needed to draw a series.",
      "⌁",
    );
    $("chart-caption").textContent = m.name;
    return;
  }
  const w = 650,
    h = 200,
    pad = 35;
  const minT = readings[0].timestamp,
    maxT = readings.at(-1).timestamp;
  const vals = readings.map((r) => r.value * m.scale + m.offset);
  let min = Math.min(...vals),
    max = Math.max(...vals);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const x = (t) => pad + ((t - minT) / (maxT - minT)) * (w - pad - 15),
    y = (v) => h - 25 - ((v - min) / (max - min)) * (h - 45);
  let path = "";
  readings.forEach((r, i) => {
    const command =
      i && r.timestamp - readings[i - 1].timestamp <= m.max_gap_seconds
        ? "L"
        : "M";
    path += `${command}${x(r.timestamp).toFixed(1)},${y(vals[i]).toFixed(1)} `;
  });
  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map((v) => {
      const yy = y(min + (max - min) * v);
      return `<line x1="${pad}" y1="${yy}" x2="${w}" y2="${yy}" stroke="#eef2e8"/><text x="0" y="${yy + 3}" fill="#a6b09a" font-size="8">${fmt(min + (max - min) * v, 0)}</text>`;
    })
    .join("");
  const dates = [0, 0.25, 0.5, 0.75, 1]
    .map((v) => {
      const t = minT + (maxT - minT) * v;
      return `<text x="${x(t)}" y="199" text-anchor="middle" fill="#a6b09a" font-size="8">${escape(new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }))}</text>`;
    })
    .join("");
  $("usage-chart").innerHTML =
    `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${escape(m.name)} recorded values"><title>${escape(m.name)} recorded readings</title>${grid}<path d="${path}" fill="none" stroke="#75985d" stroke-width="2" stroke-linejoin="round"/>${dates}</svg>`;
  $("chart-caption").textContent =
    `${m.name} · ${d.total_samples.toLocaleString()} recorded samples · ${m.mode === "rate" ? "input rate" : m.unit} · gaps remain visible`;
}
function renderMeters() {
  const q = $("meter-search").value.toLowerCase();
  $("meter-grid").innerHTML =
    state.config.meters
      .filter((m) => (m.name + " " + m.category).toLowerCase().includes(q))
      .map((m) => {
        const status = meterStatus(m);
        const latest = state.status.latest?.[m.id];
        const usage = state.report?.meters.find((r) => r.meter_id === m.id);
        return `<article class="panel meter-card"><div class="meter-card-head"><span class="tag ${status.type}">${escape(status.label)}</span><button class="icon-button edit-meter" data-id="${m.id}" aria-label="Edit ${escape(m.name)}">⋯</button></div><h3>${escape(m.name)}</h3><span class="muted" style="font-size:10px">${escape(m.category)} · ${escape(m.mode)}</span><div class="meter-value">${m.mode === "virtual" ? fmt(usage?.quantity) : latest ? fmt(latest.value * m.scale + m.offset) : "—"} <small>${escape(m.unit)}${m.mode === "rate" ? "/" + (m.rate_period_seconds === 60 ? "min" : "h") : ""}</small></div><div class="meter-card-foot"><span>${m.billable ? "Electric billing enabled" : m.mode === "virtual" ? "Net usage in report period" : "Measurement input"}</span><span>${latest ? new Date(latest.timestamp * 1000).toLocaleDateString() : "No readings"}</span></div></article>`;
      })
      .join("") ||
    empty(
      "Build your meter inventory",
      "Add a meter or discover inputs on your network.",
      "◉",
    );
  document
    .querySelectorAll(".edit-meter")
    .forEach((b) => (b.onclick = () => editMeter(b.dataset.id)));
}
function dialog(title, body, className = "") {
  $("editor").className = className;
  $("editor-title").textContent = title;
  $("editor-body").innerHTML = body;
  if (!$("editor").open) $("editor").showModal();
}
function formError(error) {
  const target = $("editor-error");
  if (target) target.textContent = error.message;
  else toast(error.message, true);
}
function defaultMeter() {
  return {
    id: uid("meter"),
    name: "",
    category: "Electrical",
    unit: "kWh",
    mode: "counter",
    source_id: null,
    point: {},
    scale: 1,
    offset: 0,
    rate_period_seconds: 3600,
    interval_seconds: 900,
    max_gap_seconds: 900,
    rollover: null,
    terms: [],
    enabled: true,
    billable: false,
    markup_percent: null,
    x: 60 + (state.config.meters.length % 5) * 280,
    y: 60 + Math.floor(state.config.meters.length / 5) * 240,
  };
}
function editMeter(id, preset = {}) {
  const existing = state.config.meters.find((m) => m.id === id);
  const m = {
    ...defaultMeter(),
    ...structuredClone(existing || {}),
    ...preset,
  };
  dialog(
    existing
      ? "Edit meter"
      : m.mode === "virtual"
        ? "Create net meter"
        : "Add meter",
    `<form id="meter-form" class="form-grid"><label class="full">Meter name<input id="m-name" value="${escape(m.name)}" required maxlength="120"></label><label>System<select id="m-category">${categories.map((c) => option(c, c, m.category)).join("")}</select></label><label>Usage unit<select id="m-unit" required>${option("", "Choose usage unit", m.unit)}${units.map((u) => option(u, u, m.unit)).join("")}</select></label><label>Reading type<select id="m-mode">${[
      ["counter", "Cumulative counter"],
      ["rate", "Instantaneous rate"],
      ["interval", "Interval quantity"],
      ["virtual", "Calculated net meter"],
    ]
      .map(([v, l]) => option(v, l, m.mode))
      .join(
        "",
      )}</select></label><label>Markup override (%)<input id="m-markup" type="number" min="0" max="1000" step="0.01" value="${m.markup_percent ?? ""}" placeholder="Use workspace markup"></label><div id="physical-fields" class="full form-grid"><label class="full">Source<select id="m-source">${option("", "Manual / imported readings", m.source_id || "")}${state.config.sources.map((s) => option(s.id, s.name, m.source_id)).join("")}</select></label><label class="full">Point reference (JSON)<textarea id="m-point" rows="3">${escape(JSON.stringify(m.point, null, 2))}</textarea></label><p id="point-help" class="form-help full"></p><label class="check-row full"><input id="m-commissioned" type="checkbox" ${m.point.commissioned ? "checked" : ""}> Source display, units and scaling have been checked for billing</label><label>Multiplier / scale<input id="m-scale" type="number" step="any" value="${m.scale}" required></label><label>Offset<input id="m-offset" type="number" step="any" value="${m.offset}" required></label><label>Rate basis (seconds)<input id="m-rate-period" type="number" min="1" max="86400" value="${m.rate_period_seconds}"></label><label>Interval duration (seconds)<input id="m-interval" type="number" min="1" max="86400" value="${m.interval_seconds}"></label><label>Maximum sample gap (seconds)<input id="m-gap" type="number" min="1" max="86400" value="${m.max_gap_seconds}"></label><label>Counter rollover modulus (optional)<input id="m-rollover" type="number" min="0.0001" step="any" value="${m.rollover ?? ""}"></label><p class="form-help full">For kW → kWh use a rate basis of 3600. For gallons/minute → gallons use 60. Counter deltas are distributed uniformly between verified samples. Gaps longer than your limit are excluded.</p></div><div id="formula-fields" class="full"><label>Signed formula inputs</label><div id="term-list" style="margin-top:12px"></div><button id="add-term" class="secondary small" type="button">＋ Add input</button><p class="form-help" style="margin-top:12px">Use +1 to add a supply and −1 to subtract a return. Input units must match. Formula cycles are rejected.</p></div><label class="check-row"><input id="m-enabled" type="checkbox" ${m.enabled ? "checked" : ""}> Enabled</label><label class="check-row"><input id="m-billable" type="checkbox" ${m.billable ? "checked" : ""}> Price electric usage</label><label class="check-row full"><input id="m-account-charges" type="checkbox" ${m.electricity_charges === "account" ? "checked" : ""}> Include account-level demand and fixed charges</label><p class="form-help full">Leave account charges off for tenant sub-meters. Shared account charges can be entered once as a bill and allocated by usage.</p><p id="editor-error" class="dialog-error"></p><div class="dialog-actions">${existing ? '<button id="delete-meter" type="button" class="secondary danger">Remove meter</button>' : ""}<button class="primary" type="submit">Save meter</button></div></form>`,
  );
  const terms = m.terms.length
    ? structuredClone(m.terms)
    : [
        {
          meter_id: state.config.meters.find((x) => x.id !== m.id)?.id || "",
          factor: 1,
        },
      ];
  function drawTerms() {
    const peers = state.config.meters.filter((x) => x.id !== m.id);
    $("term-list").innerHTML = terms
      .map(
        (t, i) =>
          `<div class="term-row"><select data-term="${i}" aria-label="Input meter">${option("", "Select input", t.meter_id)}${peers.map((p) => option(p.id, p.name + " · " + p.unit, t.meter_id)).join("")}</select><input type="number" step="any" value="${t.factor}" data-factor="${i}" aria-label="Signed multiplier"><button type="button" data-remove="${i}" aria-label="Remove input">×</button></div>`,
      )
      .join("");
    document
      .querySelectorAll("[data-term]")
      .forEach(
        (el) =>
          (el.onchange = () => (terms[+el.dataset.term].meter_id = el.value)),
      );
    document
      .querySelectorAll("[data-factor]")
      .forEach(
        (el) =>
          (el.oninput = () =>
            (terms[+el.dataset.factor].factor = Number(el.value))),
      );
    document.querySelectorAll("[data-remove]").forEach(
      (el) =>
        (el.onclick = () => {
          terms.splice(+el.dataset.remove, 1);
          drawTerms();
        }),
    );
  }
  drawTerms();
  $("add-term").onclick = () => {
    terms.push({ meter_id: "", factor: 1 });
    drawTerms();
  };
  function fields() {
    const virtual = $("m-mode").value === "virtual";
    $("physical-fields").hidden = virtual;
    $("formula-fields").hidden = !virtual;
    $("physical-fields")
      .querySelectorAll("input,select,textarea")
      .forEach((el) => (el.disabled = virtual));
  }
  fields();
  $("m-mode").onchange = fields;
  function pointHelp() {
    const s = state.config.sources.find((s) => s.id === $("m-source").value);
    $("m-point").closest("label").hidden = s?.kind === "sql_server";
    if (s?.kind === "sql_server") {
      $("point-help").textContent =
        "SQL trend: " +
        (m.point.expected_name ||
          "Choose the point in Sources & discovery first") +
        ". Historical imports and incremental sync are managed on the source page.";
      return;
    }
    $("point-help").textContent =
      s?.kind === "bacnet"
        ? "BACnet: address, object_type and object_instance. Discovery fills these in for you."
        : s?.kind === "modbus"
          ? "Modbus: register (zero-based), table (holding/input), encoding (float32/uint16/uint32/int32/float64/uint64), byte_order and word_order."
          : "HTTP: path and optional timestamp_path (Unix seconds). Local sample store: point_id. Manual readings need no reference.";
  }
  pointHelp();
  $("m-source").onchange = pointHelp;
  $("meter-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await action(e.submitter, async () => {
        const mode = $("m-mode").value;
        const updated = {
          ...m,
          name: $("m-name").value.trim(),
          category: $("m-category").value,
          unit: $("m-unit").value,
          mode,
          source_id: mode === "virtual" ? null : $("m-source").value || null,
          point:
            mode === "virtual" ? {} : JSON.parse($("m-point").value || "{}"),
          scale: Number($("m-scale").value),
          offset: Number($("m-offset").value),
          rate_period_seconds: Number($("m-rate-period").value),
          interval_seconds: Number($("m-interval").value),
          max_gap_seconds: Number($("m-gap").value),
          rollover: $("m-rollover").value
            ? Number($("m-rollover").value)
            : null,
          terms: mode === "virtual" ? terms : [],
          enabled: $("m-enabled").checked,
          billable: $("m-billable").checked,
          electricity_charges: $("m-account-charges").checked
            ? "account"
            : "energy_only",
          markup_percent: $("m-markup").value
            ? Number($("m-markup").value)
            : null,
        };
        if (mode === "virtual" && terms.some((t) => !t.meter_id))
          throw Error("Choose every formula input");
        if (updated.billable && updated.unit !== "kWh")
          throw Error(
            "Electric tariff pricing requires kWh. Use Bills & allocation for other utilities.",
          );
        if (mode !== "virtual")
          updated.point.commissioned = $("m-commissioned").checked;
        if (
          updated.source_id &&
          state.config.sources.find((s) => s.id === updated.source_id)?.kind ===
            "bacnet" &&
          (!updated.point.expected_name ||
            JSON.stringify(updated.point) !== JSON.stringify(m.point))
        )
          updated.point = await api("/api/verify-point", {
            method: "POST",
            body: { source_id: updated.source_id, point: updated.point },
          });
        const cfg = copyConfig();
        const ix = cfg.meters.findIndex((x) => x.id === m.id);
        if (ix < 0) cfg.meters.push(updated);
        else cfg.meters[ix] = updated;
        await saveConfig(cfg);
        $("editor").close();
        await load();
        if (state.page === "canvas") renderCanvas();
        toast("Meter saved");
      });
    } catch (e) {
      formError(e);
    }
  };
  if (existing)
    $("delete-meter").onclick = async () => {
      try {
        const cfg = copyConfig();
        if (
          cfg.meters.some((x) => x.terms.some((t) => t.meter_id === m.id)) ||
          state.expenses.some((e) =>
            e.allocations.some((a) => a.meter_id === m.id),
          )
        )
          throw Error(
            "Remove this meter from formulas and bill allocations before removing it",
          );
        cfg.meters = cfg.meters.filter((x) => x.id !== m.id);
        await saveConfig(cfg);
        $("editor").close();
        await load();
        toast("Meter removed");
      } catch (e) {
        formError(e);
      }
    };
}
function renderCanvas() {
  $("meter-canvas").style.width =
    Math.max(1800, ...state.config.meters.map((m) => m.x + 300)) + "px";
  $("meter-canvas").style.height =
    Math.max(900, ...state.config.meters.map((m) => m.y + 300)) + "px";
  const usage = new Map(
    (state.report?.meters || []).map((m) => [m.meter_id, m]),
  );
  $("canvas-nodes").innerHTML = state.config.meters
    .map(
      (m) =>
        `<div class="canvas-node ${m.mode === "virtual" ? "virtual" : ""}" data-mid="${m.id}" style="left:${m.x}px;top:${m.y}px" tabindex="0" role="group" aria-label="${escape(m.name)}. Drag to move; use arrow keys to adjust; Enter to edit."><div class="node-head"><span>${m.mode === "virtual" ? "CALCULATED METER" : "METER INPUT"}</span><button class="icon-button" style="font-size:14px" data-edit="${m.id}" aria-label="Edit formula or meter">⋯</button></div><div class="node-body"><strong>${escape(m.name)}</strong><div class="node-quantity">${fmt(usage.get(m.id)?.quantity)} <small>${escape(m.unit)}</small></div></div>${m.mode === "virtual" ? `<div class="node-formula">${m.terms.map((t) => `${t.factor >= 0 ? "+" : ""}${t.factor} × ${escape(state.config.meters.find((x) => x.id === t.meter_id)?.name || "Unknown")}`).join("<br>")}</div>` : ""}<span class="node-port"></span></div>`,
    )
    .join("");
  if (!state.config.meters.length)
    $("canvas-nodes").innerHTML = empty(
      "Start with your measurements",
      "Add physical meters, then create a net meter to combine them.",
      "⌘",
    );
  drawLines();
  document.querySelectorAll("[data-edit]").forEach(
    (b) =>
      (b.onclick = (e) => {
        e.stopPropagation();
        editMeter(b.dataset.edit);
      }),
  );
  document.querySelectorAll(".canvas-node").forEach((node) => {
    let origin = null,
      moved = false;
    node.onpointerdown = (e) => {
      if (e.target.closest("button")) return;
      e.preventDefault();
      node.focus();
      origin = {
        x: e.clientX,
        y: e.clientY,
        left: parseFloat(node.style.left),
        top: parseFloat(node.style.top),
      };
      moved = false;
      node.setPointerCapture(e.pointerId);
      node.classList.add("dragging");
    };
    node.onpointermove = (e) => {
      if (!origin) return;
      const x = Math.max(
          0,
          Math.min(
            parseFloat($("meter-canvas").style.width) - 250,
            origin.left + e.clientX - origin.x,
          ),
        ),
        y = Math.max(
          0,
          Math.min(
            parseFloat($("meter-canvas").style.height) - 220,
            origin.top + e.clientY - origin.y,
          ),
        );
      node.style.left = x + "px";
      node.style.top = y + "px";
      moved = true;
      drawLines();
    };
    node.onpointerup = async () => {
      node.classList.remove("dragging");
      origin = null;
      if (moved) await savePosition(node).catch((e) => toast(e.message, true));
    };
    node.onkeydown = async (e) => {
      if (e.target !== node) return;
      if (e.key === "Enter") {
        editMeter(node.dataset.mid);
        return;
      }
      const delta = {
        ArrowLeft: [-10, 0],
        ArrowRight: [10, 0],
        ArrowUp: [0, -10],
        ArrowDown: [0, 10],
      }[e.key];
      if (!delta) return;
      e.preventDefault();
      node.style.left =
        Math.max(0, parseFloat(node.style.left) + delta[0]) + "px";
      node.style.top =
        Math.max(0, parseFloat(node.style.top) + delta[1]) + "px";
      drawLines();
      await savePosition(node).catch((e) => toast(e.message, true));
    };
  });
}
function drawLines() {
  const nodes = new Map(
    [...document.querySelectorAll(".canvas-node")].map((n) => [
      n.dataset.mid,
      n,
    ]),
  );
  let lines = "";
  state.config.meters.forEach((m) =>
    m.terms.forEach((t) => {
      const a = nodes.get(t.meter_id),
        b = nodes.get(m.id);
      if (!a || !b) return;
      const x1 = parseFloat(a.style.left) + 220,
        y1 = parseFloat(a.style.top) + 60,
        x2 = parseFloat(b.style.left),
        y2 = parseFloat(b.style.top) + 60;
      const color = t.factor < 0 ? "#cb9e80" : "#a5b98e";
      lines += `<path d="M${x1} ${y1} C${x1 + 85} ${y1},${x2 - 85} ${y2},${x2} ${y2}" fill="none" stroke="${color}" stroke-width="1.6" ${t.factor < 0 ? 'stroke-dasharray="5 4"' : ""}/><circle cx="${x2}" cy="${y2}" r="3" fill="${color}"/>`;
    }),
  );
  $("canvas-lines").innerHTML = lines;
}
let positionQueue = Promise.resolve();
function savePosition(node) {
  const id = node.dataset.mid,
    x = parseFloat(node.style.left),
    y = parseFloat(node.style.top);
  positionQueue = positionQueue
    .catch(() => {})
    .then(async () => {
      const cfg = copyConfig();
      const m = cfg.meters.find((m) => m.id === id);
      if (!m) return;
      m.x = x;
      m.y = y;
      await saveConfig(cfg);
    });
  return positionQueue;
}
function renderRates() {
  const r = state.config.rate;
  $("rate-schedule").value = r.schedule;
  $("rate-option").value = r.option;
  $("rate-voltage").value = r.voltage;
  $("rate-service").value = r.service;
  $("rate-class").value = r.customer_class;
  $("rate-phase").value = r.phase;
  $("rate-markup").value = state.config.markup_percent;
  const sync = state.status.rate_sync || {};
  $("rate-status").innerHTML = sync.error
    ? `<div class="info-strip">${escape(sync.error)}</div>`
    : sync.last_success
      ? `<span class="tag">Last verified ${escape(new Date(sync.last_success * 1000).toLocaleString())}</span>`
      : '<div class="info-strip">Choose your service details to import a published tariff. No sample prices are used.</div>';
  $("rate-library").innerHTML = (state.status.tariffs || [])
    .map(
      (t) =>
        `<div class="rate-card"><h3>${escape(t.selection.schedule)} · ${escape(t.selection.option === "base" ? "Standard" : "Option " + t.selection.option)} · ${escape(t.selection.voltage)}</h3><p class="rate-metadata">Effective ${escape(t.effective_from)}${t.effective_to ? " to " + escape(t.effective_to) + " (exclusive)" : " onward"}<br>${escape(t.selection.service)} · ${escape(t.sheet)}</p>${Object.entries(
          t.energy,
        )
          .map(
            ([season, rates]) =>
              `<p class="eyebrow" style="margin-top:16px">${season.toUpperCase()}</p>${Object.entries(
                rates,
              )
                .map(
                  ([p, v]) =>
                    `<div class="rate-cell"><span>${escape(p.replaceAll("_", " "))}</span><strong>$${v.toFixed(5)} / kWh</strong></div>`,
                )
                .join("")}`,
          )
          .join(
            "",
          )}<div class="rate-cell"><span>Demand components</span><strong>${t.demand.length}</strong></div><div class="rate-cell"><span>Customer charge</span><strong>${cash(t.customer_daily)} / day</strong></div><p class="rate-metadata">Source checksum: ${escape(t.source_sha256.slice(0, 20))}…</p><a class="inline-link" target="_blank" rel="noreferrer" href="${escape(t.source_url)}">Official source workbook ↗</a></div>`,
    )
    .join("");
}
function renderSources() {
  $("source-list").innerHTML =
    state.config.sources
      .map(
        (s) =>
          `<div class="source-item"><div class="source-row"><div><h3>${escape(s.name)}</h3><p class="muted">${escape({ bacnet: "BACnet / IP", modbus: "Modbus TCP", http_json: "HTTP JSON", platform_sqlite: "Local sample database", sql_server: "Microsoft SQL Server trends" }[s.kind])} · ${s.enabled ? "Enabled" : "Disabled"}</p></div><div class="source-actions"><button class="secondary small" data-source-edit="${s.id}">Configure</button><button class="primary small" data-discover="${s.id}">${s.kind === "sql_server" ? "Browse trend points" : "Discover " + (s.kind === "bacnet" ? "devices" : "points")} ↗</button>${s.kind === "sql_server" ? `<button class="secondary small" data-sql-import="${s.id}">Import trends</button>` : ""}</div></div></div>`,
      )
      .join("") ||
    `<article class="panel">${empty("Connect a source", "Add a network reader or a local read-only data connection.", "⌁")}</article>`;
  document
    .querySelectorAll("[data-source-edit]")
    .forEach((b) => (b.onclick = () => editSource(b.dataset.sourceEdit)));
  document
    .querySelectorAll("[data-discover]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          action(b, () =>
            state.config.sources.find((s) => s.id === b.dataset.discover)
              ?.kind === "sql_server"
              ? openSqlSource(b.dataset.discover)
              : discover(b.dataset.discover),
          ).catch(() => {})),
    );
}
function bindSqlSourceActions() {
  document
    .querySelectorAll("[data-sql-import]")
    .forEach((b) => (b.onclick = () => openSqlImport(b.dataset.sqlImport)));
  if (typeof renderSqlJobs === "function") renderSqlJobs();
}
function editSource(id) {
  const existing = state.config.sources.find((s) => s.id === id);
  const source = structuredClone(
    existing || {
      id: uid("source"),
      name: "",
      kind: "bacnet",
      enabled: true,
      settings: {},
    },
  );
  dialog(
    existing ? "Configure source" : "Add a meter source",
    `<form id="source-form" class="form-grid"><label class="full">Connection name<input id="s-name" value="${escape(source.name)}" required maxlength="120"></label><label class="full">Protocol<select id="s-kind">${[
      ["bacnet", "BACnet / IP"],
      ["modbus", "Modbus TCP"],
      ["http_json", "HTTP JSON"],
      ["platform_sqlite", "Local automation sample database (read only)"],
      ["sql_server", "Microsoft SQL Server trends"],
    ]
      .map(([v, l]) => option(v, l, source.kind))
      .join(
        "",
      )}</select></label><div id="source-fields" class="full form-grid"></div><label class="check-row full"><input id="s-enabled" type="checkbox" ${source.enabled ? "checked" : ""}> Enable this source</label><p id="editor-error" class="dialog-error"></p><div class="dialog-actions">${existing ? '<button type="button" class="secondary danger" id="delete-source">Remove source</button>' : ""}<button class="primary">Save connection</button></div></form>`,
  );
  function fields() {
    const kind = $("s-kind").value;
    const s = kind === source.kind ? source.settings : {};
    if (kind === "bacnet")
      $("source-fields").innerHTML =
        `<label class="full">Reader bind address / prefix : port<input id="s-bind" value="${escape(s.bind || "")}" placeholder="Your interface IPv4/prefix:unused-port" required></label><label class="full">Discovery destination<input id="s-target" value="${escape(s.target || "")}" placeholder="Device IPv4:47808 or subnet broadcast:47808" required></label><label>Reader device instance<input id="s-instance" type="number" min="0" max="4194302" value="${s.instance ?? 4194290}" required></label><label>Optional directed scan CIDR<input id="s-cidr" value="${escape(s.scan_cidr || "")}" placeholder="Your private subnet /24 or smaller"></label><p class="form-help full">Use an unused reader port such as 47810. Port 47808 is reserved for the existing service. Discovery sends one Who-Is to the destination; an optional private CIDR also probes each host at five requests/second. Routed devices may need a reachable router address. This reader does not register with a BBMD or write properties.</p>`;
    if (kind === "modbus")
      $("source-fields").innerHTML =
        `<label class="full">Device host<input id="s-host" value="${escape(s.host || "")}" required></label><label>TCP port<input id="s-port" type="number" min="1" max="65535" value="${s.port ?? 502}" required></label><label>Unit ID<input id="s-unit" type="number" min="0" max="247" value="${s.unit_id ?? 1}" required></label><label>Probe register (zero-based)<input id="s-register" type="number" min="0" max="65535" value="${s.probe_register ?? 0}"></label><label>Probe register count<input id="s-count" type="number" min="1" max="32" value="${s.probe_count ?? 4}"></label><p class="form-help full">Discovery reads only this configured block. Use the manufacturer's register map to configure each meter's encoding, scale and unit. Serial ports are never opened.</p>`;
    if (kind === "http_json")
      $("source-fields").innerHTML =
        `<label class="full">JSON endpoint URL<input id="s-url" type="url" value="${escape(s.url || "")}" required></label><label class="full">Request headers (JSON, optional)<textarea id="s-headers">${escape(JSON.stringify(s.headers || {}, null, 2))}</textarea></label><p class="form-help full">Nested numeric fields become selectable points. Tokens remain in the private configuration database. Responses must fit within 2 MB.</p>`;
    if (kind === "sql_server")
      $("source-fields").innerHTML = sqlSourceFields(s);
    if (kind === "platform_sqlite")
      $("source-fields").innerHTML =
        `<label class="full">Absolute path to existing sample database<input id="s-path" value="${escape(s.path || "")}" required></label><p class="form-help full">Uses SQLite mode=ro and query_only against the current_samples table. It does not modify the source database, software or serial links. Only numeric points are offered as meter candidates.</p>`;
  }
  fields();
  $("s-kind").onchange = fields;
  $("source-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await action(e.submitter, async () => {
        const kind = $("s-kind").value;
        let settings = {};
        if (kind === "bacnet")
          settings = {
            bind: $("s-bind").value.trim(),
            target: $("s-target").value.trim(),
            instance: Number($("s-instance").value),
            scan_cidr: $("s-cidr").value.trim(),
          };
        if (kind === "modbus")
          settings = {
            host: $("s-host").value.trim(),
            port: Number($("s-port").value),
            unit_id: Number($("s-unit").value),
            probe_register: Number($("s-register").value),
            probe_count: Number($("s-count").value),
          };
        if (kind === "http_json")
          settings = {
            url: $("s-url").value.trim(),
            headers: JSON.parse($("s-headers").value || "{}"),
          };
        if (kind === "platform_sqlite")
          settings = { path: $("s-path").value.trim() };
        if (kind === "sql_server")
          settings = readSqlSourceFields(
            source.kind === kind ? source.settings : {},
          );
        const cfg = copyConfig();
        const updated = {
          ...source,
          name: $("s-name").value.trim(),
          kind,
          settings,
          enabled: $("s-enabled").checked,
        };
        const ix = cfg.sources.findIndex((s) => s.id === source.id);
        if (ix < 0) cfg.sources.push(updated);
        else cfg.sources[ix] = updated;
        await saveConfig(cfg);
        if (kind === "sql_server") $("sql-password").value = "";
        $("editor").close();
        renderSources();
        bindSqlSourceActions();
        toast("Connection saved");
        if (kind === "sql_server") await openSqlSource(source.id);
      });
    } catch (e) {
      formError(e);
    }
  };
  if (existing)
    $("delete-source").onclick = async () => {
      try {
        if (state.config.meters.some((m) => m.source_id === source.id))
          throw Error("Reassign or remove this source’s meters first");
        const cfg = copyConfig();
        cfg.sources = cfg.sources.filter((s) => s.id !== source.id);
        await saveConfig(cfg);
        if ($("sql-password")) $("sql-password").value = "";
        $("editor").close();
        renderSources();
        bindSqlSourceActions();
        if (typeof sqlView !== "undefined" && sqlView.sourceId === source.id) {
          sqlView.sourceId = null;
          sqlView.selected.clear();
          $("sql-workspace").hidden = true;
        }
        toast("Source removed");
      } catch (e) {
        formError(e);
      }
    };
}
async function discover(sourceId, device = null, offset = 1) {
  $("discovery-results").hidden = false;
  $("discovery-body").innerHTML = empty(
    "Reading network inventory…",
    "Discovery is bounded and sends read requests only. A subnet scan can take about a minute.",
    "⌁",
  );
  $("discovery-results").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const result = await api("/api/discover", {
      method: "POST",
      body: { source_id: sourceId, device, offset, limit: 80 },
    });
    state.discovery = { ...result, sourceId, device, offset };
    $("point-filter").value = "";
    renderDiscovery();
  } catch (e) {
    $("discovery-body").innerHTML = empty(
      "Connection needs attention",
      e.message,
      "⌁",
    );
    throw e;
  }
}
function mapUnit(value) {
  const u = String(value).toLowerCase();
  return (
    {
      "kilowatt-hours": ["kWh", "counter", 3600],
      "watt-hours": ["kWh", "counter", 3600, 0.001],
      kilowatts: ["kWh", "rate", 3600],
      watts: ["kWh", "rate", 3600, 0.001],
      "us-gallons": ["gal", "counter", 3600],
      gallons: ["gal", "counter", 3600],
      "us-gallons-per-minute": ["gal", "rate", 60],
      "cubic-meters": ["m3", "counter", 3600],
      "cubic-meters-per-hour": ["m3", "rate", 3600],
      liters: ["L", "counter", 3600],
      therms: ["therm", "counter", 3600],
      kilobtus: ["kBtu", "counter", 3600],
      btus: ["kBtu", "counter", 3600, 0.001],
      "kilowatt-hours-thermal": ["kWh-thermal", "counter", 3600],
    }[u] || ["", "counter", 3600]
  );
}

async function scanDevice(sourceId, device) {
  const job = await api("/api/discovery-jobs", {
    method: "POST",
    body: { source_id: sourceId, device },
  });
  $("discovery-body").innerHTML =
    empty(
      "Scanning device inventory…",
      "This scan reads up to 10,000 objects and can take several minutes. Meter polling continues between pages.",
      "⌁",
    ) +
    '<p id="scan-progress" class="form-help"></p><button id="cancel-scan" class="secondary small">Cancel scan</button>';
  $("cancel-scan").onclick = () =>
    api("/api/discovery-jobs/" + job.id, { method: "DELETE" }).catch((e) =>
      toast(e.message, true),
    );
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const result = await api("/api/discovery-jobs/" + job.id);
    if ($("scan-progress"))
      $("scan-progress").textContent =
        `${result.offset} / ${result.object_count || "?"} objects · ${result.points.length} analog points`;
    if (result.done) {
      state.discovery = {
        sourceId,
        device,
        offset: 1,
        fullScan: true,
        ...result,
      };
      $("point-filter").value = "";
      renderDiscovery();
      if (result.error) toast(result.error, true);
      break;
    }
  }
}

function renderDiscovery() {
  const d = state.discovery;
  if (!d) return;
  $("discovery-heading").textContent = d.device
    ? "Points · " + (d.device.name || d.device.address)
    : "Discovery results";
  if (d.devices) {
    $("discovery-body").innerHTML =
      d.devices
        .map(
          (dev, i) =>
            `<div class="source-item"><div class="source-row"><div><h3>${escape(dev.name)}</h3><p class="muted">${escape(dev.address)} · Device ${dev.instance}</p></div><div class="source-actions"><button class="secondary small" data-device="${i}">List points ↗</button><button class="primary small" data-scan-device="${i}">Scan all points</button></div></div></div>`,
        )
        .join("") ||
      empty(
        "No devices replied",
        "Check the bind, target port, VLAN or routed access. A directed CIDR scan can find devices that ignore subnet broadcasts.",
      );
    document
      .querySelectorAll("[data-device]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            action(b, () =>
              discover(d.sourceId, d.devices[+b.dataset.device]),
            ).catch(() => {})),
      );
    document
      .querySelectorAll("[data-scan-device]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            action(b, () =>
              scanDevice(d.sourceId, d.devices[+b.dataset.scanDevice]),
            ).catch(() => {})),
      );
    return;
  }
  if (d.registers) {
    $("discovery-body").innerHTML =
      `<p class="form-help">${escape(d.note)}</p><pre>${escape(JSON.stringify(d.registers))}</pre>`;
    return;
  }
  const q = $("point-filter").value.toLowerCase();
  const points = (d.points || []).filter((p) =>
    (p.name + " " + p.units + " " + p.description).toLowerCase().includes(q),
  );
  $("discovery-body").innerHTML =
    `${d.fullScan ? `<p class="form-help">${d.object_count} objects scanned; ${d.points.length} analog points found. Filter searches the full inventory. Showing up to 500 matching points.</p>` : d.object_count ? `<p class="form-help">Object indexes ${d.offset}–${Math.min(d.offset + 79, d.object_count)} of ${d.object_count}. Filter searches this page. Non-numeric object types are skipped.</p>` : ""}<div class="table-scroll"><table><thead><tr><th>Point / description</th><th>Reported unit</th><th></th></tr></thead><tbody>${
      points
        .filter((p) => p.point)
        .slice(0, 500)
        .map(
          (p) =>
            `<tr><td style="white-space:normal">${escape(p.name)}<small>${escape(p.description || "")}</small></td><td>${escape(p.units || "Unknown")}</td><td><button class="secondary small" data-add-point="${(d.points || []).indexOf(p)}">＋ Add meter</button></td></tr>`,
        )
        .join("") ||
      '<tr><td colspan="3">No matching points on this page.</td></tr>'
    }</tbody></table></div><div class="toolbar" style="margin-top:20px">${d.offset > 1 ? '<button id="previous-points" class="secondary small">← Previous page</button>' : "<span></span>"}${d.next_offset ? '<button id="next-points" class="secondary small">Next page →</button>' : ""}</div>`;
  document.querySelectorAll("[data-add-point]").forEach(
    (b) =>
      (b.onclick = () => {
        const p = d.points[+b.dataset.addPoint],
          u = mapUnit(p.units);
        editMeter(null, {
          name: p.name,
          source_id: d.sourceId,
          point: p.point,
          unit: u[0],
          mode: u[1],
          rate_period_seconds: u[2],
          scale: u[3] || 1,
          category: u[0] === "kWh" ? "Electrical" : "Other",
        });
      }),
  );
  if ($("next-points"))
    $("next-points").onclick = () =>
      action($("next-points"), () =>
        discover(d.sourceId, d.device, d.next_offset),
      ).catch(() => {});
  if ($("previous-points"))
    $("previous-points").onclick = () =>
      action($("previous-points"), () =>
        discover(d.sourceId, d.device, Math.max(1, d.offset - 80)),
      ).catch(() => {});
}
function renderExpenses() {
  $("expense-list").innerHTML =
    state.expenses
      .map((e) => {
        const a = state.report?.allocations.find((a) => a.expense_id === e.id);
        return `<article class="bill-item"><div class="bill-row"><div><p class="eyebrow">${escape(e.category.toUpperCase())} · BILLED ${escape(e.billed_on)}</p><h3>${escape(e.description)}</h3><p class="muted">${escape(e.period_start)} → ${escape(e.period_end)} (exclusive) · ${e.allocations.length} meters</p></div><div class="source-actions"><strong class="bill-amount">${cash(e.amount)}</strong><button class="secondary small" data-expense-edit="${e.id}">Edit allocation</button></div></div><div class="allocation-rows">${a ? (a.allocated ? `${a.rows.map((r) => `<div class="allocation-row"><span>${escape(state.config.meters.find((m) => m.id === r.meter_id)?.name || r.meter_id)} · ${fmt(r.quantity)} ${escape(a.unit)} × ${r.weight}</span><span>${fmt(r.share * 100, 1)}% · ${cash(r.base_cost)}</span></div>`).join("")}<p class="form-help">Weighted unit cost: $${fmt(a.unit_cost, 5)} / ${escape(a.unit)}. Allocated base costs total ${cash(a.amount)}.</p>` : `<span class="tag warning">Unallocated</span><p class="form-help" style="margin-top:10px">${a.issues.map(escape).join("<br>")}</p>`) : '<p class="form-help">Select this bill’s complete date range in Overview to calculate shares.</p>'}</div></article>`;
      })
      .join("") ||
    `<article class="panel">${empty("Bring your costs together", "Add a water, chemical, fuel or other bill to allocate it by recorded usage.", "▤")}</article>`;
  document
    .querySelectorAll("[data-expense-edit]")
    .forEach((b) => (b.onclick = () => editExpense(b.dataset.expenseEdit)));
}
function editExpense(id) {
  const existing = state.expenses.find((e) => e.id === id);
  const e = structuredClone(
    existing || {
      id: uid("bill"),
      description: "",
      category: "Other",
      billed_on: dateStr(new Date()),
      period_start: $("report-start").value,
      period_end: $("report-end").value,
      amount: "",
      allocations: [],
    },
  );
  dialog(
    existing ? "Edit bill & allocation" : "Add a shared bill",
    `<form id="expense-form" class="form-grid"><label class="full">Bill description<input id="e-description" value="${escape(e.description)}" required maxlength="200" placeholder="Water service, treatment chemicals, heating fuel…"></label><label>System<select id="e-category">${categories.map((c) => option(c, c, e.category)).join("")}</select></label><label>Bill amount ($)<input id="e-amount" type="number" min="0.01" max="1000000000" step="0.01" value="${e.amount}" required></label><label>Bill date<input id="e-date" type="date" value="${e.billed_on}" required></label><span></span><label>Usage period starts<input id="e-start" type="date" value="${e.period_start}" required></label><label>Usage period ends (exclusive)<input id="e-end" type="date" value="${e.period_end}" required></label><div class="full"><label>Allocate using these meters</label><p class="form-help" style="margin:9px 0 15px">Select meters with matching units. Weight 1 divides the cost by actual usage; other positive weights adjust each meter’s share.</p>${
      state.config.meters
        .map((m) => {
          const a = e.allocations.find((a) => a.meter_id === m.id);
          return `<div class="weight-row"><input type="checkbox" data-allocate="${m.id}" ${a ? "checked" : ""} aria-label="Allocate to ${escape(m.name)}"><span>${escape(m.name)} · ${escape(m.unit)}</span><input type="number" min="0.000001" max="1000000" step="any" value="${a?.weight || 1}" data-weight="${m.id}" aria-label="Allocation weight for ${escape(m.name)}"></div>`;
        })
        .join("") ||
      '<p class="form-help">Add meters before creating an allocation.</p>'
    }</div><p class="form-help full">Formula: bill amount × (meter usage × weight) ÷ total weighted usage. Incomplete or zero usage leaves the bill unallocated. The last cent is assigned by largest remainder.</p><p id="editor-error" class="dialog-error"></p><div class="dialog-actions">${existing ? '<button class="secondary danger" type="button" id="delete-expense">Remove bill</button>' : ""}<button class="primary">Save bill & allocation</button></div></form>`,
  );
  $("expense-form").onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await action(ev.submitter, async () => {
        const allocations = [
          ...document.querySelectorAll("[data-allocate]:checked"),
        ].map((el) => ({
          meter_id: el.dataset.allocate,
          weight: Number(
            document.querySelector(`[data-weight="${el.dataset.allocate}"]`)
              .value,
          ),
        }));
        if (!allocations.length) throw Error("Choose at least one meter");
        const body = {
          ...e,
          description: $("e-description").value.trim(),
          category: $("e-category").value,
          amount: Number($("e-amount").value),
          billed_on: $("e-date").value,
          period_start: $("e-start").value,
          period_end: $("e-end").value,
          allocations,
        };
        const result = await api("/api/expenses/" + encodeURIComponent(e.id), {
          method: "PUT",
          body,
        });
        $("editor").close();
        await load();
        toast(
          result.allocated
            ? "Bill saved and allocated"
            : "Bill saved. Usage coverage is incomplete.",
        );
      });
    } catch (err) {
      formError(err);
    }
  };
  if (existing)
    $("delete-expense").onclick = () =>
      action($("delete-expense"), async () => {
        await api("/api/expenses/" + encodeURIComponent(e.id), {
          method: "DELETE",
        });
        $("editor").close();
        await load();
        toast("Bill removed");
      }).catch(formError);
}
function renderSettings() {
  $("setting-timezone").value = state.config.timezone;
  $("setting-poll").value = state.config.poll_seconds;
  $("setting-markup").value = state.config.markup_percent;
}
async function loadAudit() {
  try {
    const rows = await api("/api/audit");
    $("audit-list").innerHTML =
      rows
        .map(
          (r) =>
            `<div class="audit-row"><span>${escape(r.action)}</span><span>${escape(new Date(r.timestamp * 1000).toLocaleString())}</span></div>`,
        )
        .join("") ||
      '<p class="form-help">Workspace changes will appear here.</p>';
  } catch (e) {
    toast(e.message, true);
  }
}
function importReadings() {
  dialog(
    "Import recorded readings",
    `<form id="readings-form" class="form-grid"><p class="form-help full">Import JSON readings or a CSV with columns <code>meter_id,timestamp,value,quality</code>. Timestamp is Unix seconds or an ISO date/time with an explicit timezone. Import at most 10,000 rows. Duplicate meter/timestamp pairs are ignored.</p><label class="full">Readings file<input id="readings-file" type="file" accept=".json,.csv" required></label><p class="form-help full">Your meter IDs:<br>${state.config.meters
      .filter((m) => m.mode !== "virtual")
      .map((m) => escape(m.name) + " → <code>" + escape(m.id) + "</code>")
      .join(
        "<br>",
      )}</p><p id="editor-error" class="dialog-error"></p><div class="dialog-actions"><button class="primary">Import readings</button></div></form>`,
  );
  function parseCSV(input) {
    const rows = [];
    let row = [],
      field = "",
      quoted = false;
    for (let i = 0; i < input.length; i++) {
      const c = input[i];
      if (c === '"') {
        if (quoted && input[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = !quoted;
      } else if (c === "," && !quoted) {
        row.push(field);
        field = "";
      } else if (c === "\n" && !quoted) {
        row.push(field.replace(/\r$/, ""));
        if (row.some((x) => x.trim())) rows.push(row);
        row = [];
        field = "";
      } else field += c;
    }
    if (quoted) throw Error("Unclosed CSV quote");
    if (field || row.length) {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
    }
    const headers = rows.shift().map((h) => h.trim());
    return rows.map((r) =>
      Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])),
    );
  }
  $("readings-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await action(e.submitter, async () => {
        const file = $("readings-file").files[0];
        if (file.size > 2_000_000)
          throw Error("Import files must be under 2 MB");
        const text = await file.text();
        let rows = file.name.toLowerCase().endsWith(".json")
          ? JSON.parse(text)
          : parseCSV(text);
        if (!Array.isArray(rows))
          throw Error("JSON file must contain an array of readings");
        rows = rows.map((r, i) => {
          let timestamp = Number(r.timestamp);
          if (!Number.isFinite(timestamp)) {
            if (!/(Z|[+-]\d\d:\d\d)$/i.test(r.timestamp))
              throw Error(
                "Row " + (i + 1) + ": timestamp needs an explicit timezone",
              );
            timestamp = Date.parse(r.timestamp) / 1000;
          }
          return {
            meter_id: r.meter_id,
            timestamp,
            value: Number(r.value),
            quality: r.quality || "good",
          };
        });
        const result = await api("/api/readings", {
          method: "POST",
          body: rows,
        });
        $("editor").close();
        await load();
        toast(result.accepted + " readings processed");
      });
    } catch (err) {
      formError(err);
    }
  };
}
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    await action(e.submitter, async () => {
      const r = await api("/api/login", {
        method: "POST",
        body: { password: $("login-password").value },
      });
      useSession(r);
      $("login-password").value = "";
      await load();
      navigate(location.hash.slice(1) || "overview");
    });
  } catch (err) {
    $("login-error").textContent = err.message;
  }
};
$("rate-form").onsubmit = async (e) => {
  e.preventDefault();
  action(e.submitter, async () => {
    const cfg = copyConfig();
    cfg.rate = {
      schedule: $("rate-schedule").value.trim(),
      option: $("rate-option").value,
      voltage: $("rate-voltage").value,
      service: $("rate-service").value,
      customer_class: $("rate-class").value,
      phase: $("rate-phase").value,
    };
    cfg.markup_percent = Number($("rate-markup").value);
    await saveConfig(cfg);
    await api("/api/rates/refresh", {
      method: "POST",
      body: state.config.rate,
    });
    await load();
    toast("Official tariff imported and billing recalculated");
  }).catch(async () => {
    state.status = await api("/api/status");
    renderRates();
  });
};
$("settings-form").onsubmit = async (e) => {
  e.preventDefault();
  action(e.submitter, async () => {
    const cfg = copyConfig();
    cfg.timezone = $("setting-timezone").value.trim();
    cfg.poll_seconds = Number($("setting-poll").value);
    cfg.markup_percent = Number($("setting-markup").value);
    await saveConfig(cfg);
    await load();
    toast("Settings saved");
  }).catch(() => {});
};
$("password-form").onsubmit = async (e) => {
  e.preventDefault();
  action(e.submitter, async () => {
    await api("/api/password", {
      method: "POST",
      body: {
        current_password: $("current-password").value,
        new_password: $("new-password").value,
      },
    });
    $("password-form").reset();
    showLogin();
    toast("Password updated. Sign in again.");
  }).catch(() => {});
};
$("close-editor").onclick = () => $("editor").close();
$("meter-search").oninput = renderMeters;
$("point-filter").oninput = renderDiscovery;
$("chart-meter").onchange = () =>
  drawChart().catch((e) => toast(e.message, true));
document
  .querySelectorAll("[data-page]")
  .forEach((b) => (b.onclick = () => navigate(b.dataset.page)));
bindAction("new-meter", () => editMeter());
bindAction("new-formula", () =>
  editMeter(null, {
    mode: "virtual",
    category: "Domestic hot water",
    unit: "gal",
    x: 750,
    y: 160,
  }),
);
bindAction("new-source", () => editSource());
bindAction("new-expense", () => editExpense());
bindAction("import-readings", importReadings);
bindAction("run-report", async () => {
  await runReport();
  renderExpenses();
  renderMeters();
  toast("Report updated");
});
bindAction("refresh-all", async () => {
  await api("/api/poll", { method: "POST" });
  await load();
  toast("Meter readings refreshed");
});
bindAction("logout", async () => {
  await api("/api/logout", { method: "POST" });
  showLogin();
});
bindAction("canvas-reset", async () => {
  const cfg = copyConfig();
  cfg.meters.forEach((m, i) => {
    m.x = 60 + (i % 5) * 290;
    m.y = 60 + Math.floor(i / 5) * 240;
  });
  await saveConfig(cfg);
  renderCanvas();
  toast("Cards arranged");
});
const now = new Date();
$("report-start").value = dateStr(
  new Date(now.getFullYear(), now.getMonth(), 1),
);
$("report-end").value = dateStr(
  new Date(now.getFullYear(), now.getMonth() + 1, 1),
);
document.addEventListener("DOMContentLoaded", () => {
  api("/api/session")
    .then(async (s) => {
      useSession(s);
      await load();
      navigate(location.hash.slice(1) || "overview");
    })
    .catch(() => showLogin());
});
setInterval(async () => {
  if (!state.csrf || $("editor").open || document.hidden) return;
  try {
    state.status = await api("/api/status");
    renderMeters();
    if (state.page === "overview") await runReport();
  } catch {}
}, 60000);
