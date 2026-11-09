"use strict";
const monthlyBilling = {
  drafts: new Map(),
  report: null,
  revision: null,
  initialized: false,
  range: null,
  coverageRequest: 0,
  timer: null,
};
const monthlyDefaults = {
  schedule: "",
  option: "base",
  voltage: "secondary",
  service: "bundled",
  customer_class: "mandatory",
  phase: "poly",
};

function followingMonth(value, count = 1) {
  const [year, month] = value.split("-").map(Number);
  const next = new Date(Date.UTC(year, month - 1 + count, 1));
  return next.toISOString().slice(0, 7);
}
function monthLabel(value) {
  return new Date(value + "-15T12:00:00Z").toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    timeZone: "America/Los_Angeles",
  });
}
function monthlyRange() {
  const start = $("monthly-from").value,
    through = $("monthly-through").value;
  if (
    !/^\d{4}-\d{2}$/.test(start) ||
    !/^\d{4}-\d{2}$/.test(through) ||
    through < start
  )
    throw Error("Choose an increasing range of calendar months");
  const end = followingMonth(through),
    months = [];
  for (let m = start; m < end; m = followingMonth(m)) {
    months.push(m);
    if (months.length > 36) throw Error("Choose at most 36 months per report");
  }
  return { start_month: start, end_month: end, months };
}
function sameMonthlySelection(a, b) {
  return Object.keys(monthlyDefaults).every(
    (key) =>
      (a?.[key] ?? monthlyDefaults[key]) === (b?.[key] ?? monthlyDefaults[key]),
  );
}
function monthlySaved(month) {
  return state.config.monthly_rates?.find((entry) => entry.month === month)
    ?.selection;
}
function readMonthlyRow(row) {
  const selection = { ...monthlyDefaults };
  for (const input of row.querySelectorAll("[data-monthly-field]"))
    selection[input.dataset.monthlyField] = input.value.trim();
  return selection;
}
function monthlyDirty() {
  return [...monthlyBilling.drafts].some(
    ([month, selection]) =>
      !sameMonthlySelection(selection, monthlySaved(month)),
  );
}
function invalidateMonthlyReport(message) {
  monthlyBilling.report = null;
  $("monthly-results").innerHTML = empty(
    "Ready for monthly billing",
    message ||
      "Save each month's schedule, fetch historical rates and calculate your monthly bills.",
    "▦",
  );
}
function renderMonthlyPlan(force = false) {
  if (!state.config) return;
  if (!monthlyBilling.initialized) {
    const current = new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      timeZone: "America/Los_Angeles",
    }).formatToParts(new Date());
    const currentMonth =
      current.find((p) => p.type === "year").value +
      "-" +
      current.find((p) => p.type === "month").value;
    $("monthly-from").value = followingMonth(currentMonth, -12);
    $("monthly-through").value = followingMonth(currentMonth, -1);
    monthlyBilling.initialized = true;
    invalidateMonthlyReport();
  }
  if (monthlyBilling.report && monthlyBilling.revision !== state.revision)
    invalidateMonthlyReport(
      "Workspace settings changed. Calculate again to update the monthly bills.",
    );
  if (!force && monthlyDirty()) return;
  const range = monthlyRange();
  monthlyBilling.range = range;
  $("monthly-plan").innerHTML = range.months
    .map((month) => {
      const s =
        monthlyBilling.drafts.get(month) ||
        monthlySaved(month) ||
        monthlyDefaults;
      const field = (key, values) =>
        `<select data-monthly-field="${key}" aria-label="${escape(key.replaceAll("_", " "))} for ${month}">${values.map(([value, title]) => option(value, title, s[key])).join("")}</select>`;
      return `<tr data-billing-month="${month}"><td><strong>${escape(monthLabel(month))}</strong><small>${month}-01 → ${followingMonth(month)}-01</small></td><td><input data-monthly-field="schedule" aria-label="Rate schedule for ${month}" list="monthly-schedules" maxlength="30" placeholder="e.g. B-19" value="${escape(s.schedule)}"></td><td>${field(
        "option",
        [
          ["base", "Standard / base"],
          ["R", "Option R"],
          ["S", "Option S"],
        ],
      )}</td><td><details class="monthly-service"><summary>${escape(s.voltage)} · ${s.service === "bundled" ? "Bundled PG&E" : "Delivery / CCA"}</summary><div class="form-grid"><label>Voltage${field(
        "voltage",
        [
          ["secondary", "Secondary"],
          ["primary", "Primary"],
          ["transmission", "Transmission"],
        ],
      )}</label><label>Service${field("service", [
        ["bundled", "Bundled PG&E"],
        ["delivery", "Delivery only / CCA"],
      ])}</label><label>B-19 customer${field("customer_class", [
        ["mandatory", "Mandatory"],
        ["voluntary", "Voluntary"],
      ])}</label><label>B-6 phase${field("phase", [
        ["poly", "Polyphase"],
        ["single", "Single phase"],
      ])}</label></div></details></td><td data-monthly-coverage>Checking saved rates…</td></tr>`;
    })
    .join("");
  $("monthly-plan").oninput = (event) => {
    const row = event.target.closest("[data-billing-month]");
    if (!row) return;
    const s = readMonthlyRow(row);
    monthlyBilling.drafts.set(row.dataset.billingMonth, s);
    row.querySelector("summary").textContent =
      s.voltage +
      " · " +
      (s.service === "bundled" ? "Bundled PG&E" : "Delivery / CCA");
    row.querySelector("[data-monthly-coverage]").textContent =
      "Save to check rates";
    $("monthly-save-status").textContent = "Unsaved monthly schedule changes";
    invalidateMonthlyReport(
      "Monthly schedules changed. Save and calculate again.",
    );
  };
  refreshMonthlyCoverage().catch((e) => toast(e.message, true));
}
async function refreshMonthlyCoverage() {
  const range = monthlyBilling.range;
  if (!range) return;
  const request = ++monthlyBilling.coverageRequest;
  const data = await api(
    "/api/rates/monthly-coverage?" +
      new URLSearchParams({
        start_month: range.start_month,
        end_month: range.end_month,
      }),
  );
  if (request !== monthlyBilling.coverageRequest) return;
  for (const month of data.months) {
    const row = document.querySelector(`[data-billing-month="${month.month}"]`);
    if (!row) continue;
    const cell = row.querySelector("[data-monthly-coverage]");
    if (!sameMonthlySelection(readMonthlyRow(row), month.selection)) {
      cell.textContent = "Save to check rates";
      continue;
    }
    cell.innerHTML = month.selection
      ? `<span class="tag ${month.complete ? "" : "warning"}">${month.covered_days} / ${month.total_days} days</span>`
      : '<span class="tag warning">No schedule assigned</span>';
  }
}
async function saveMonthlyPlan() {
  if (!monthlyBilling.range) throw Error("Load the billing months first");
  const requested = monthlyRange();
  if (
    requested.start_month !== monthlyBilling.range.start_month ||
    requested.end_month !== monthlyBilling.range.end_month
  )
    throw Error("Click Load months after changing the billing range");
  const cfg = copyConfig(),
    saved = new Map(
      (cfg.monthly_rates || []).map((r) => [r.month, r.selection]),
    );
  for (const [month, selection] of monthlyBilling.drafts) {
    if (selection.schedule) saved.set(month, selection);
    else saved.delete(month);
  }
  cfg.monthly_rates = [...saved]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, selection]) => ({ month, selection }));
  if (monthlyDirty()) await saveConfig(cfg);
  monthlyBilling.drafts.clear();
  $("monthly-save-status").textContent =
    "Monthly schedules saved. Prices are matched to their official effective dates.";
  if (monthlyBilling.report && monthlyBilling.revision !== state.revision)
    invalidateMonthlyReport(
      "Schedules saved. Calculate again to update the monthly bills.",
    );
  renderMonthlyPlan(true);
}
function monthlyNotes(month) {
  const allocated = new Set(
    month.allocations
      .filter((a) => a.allocated)
      .flatMap((a) => a.rows.map((r) => r.meter_id)),
  );
  return [
    ...new Set([
      ...month.notes,
      ...month.meters
        .filter((r) => r.electricity || allocated.has(r.meter_id))
        .flatMap((r) =>
          [...r.issues, ...(r.electricity?.notes || [])].map(
            (n) => r.name + ": " + n,
          ),
        ),
    ]),
  ];
}
function renderMonthlyBills(report) {
  monthlyBilling.report = report;
  monthlyBilling.revision = state.revision;
  const total = report.totals,
    max = Math.max(1, ...report.months.map((m) => m.total_cost));
  const query = new URLSearchParams({
    start_month: report.start_month,
    end_month: report.end_month,
  });
  $("monthly-results").innerHTML =
    `<div class="stats-grid monthly-stats"><article class="stat-card"><p>${report.complete ? "Calculated total" : "Priced subtotal"}</p><strong>${cash(total.total_cost)}</strong><small>${report.months.length} monthly bills</small></article><article class="stat-card"><p>Complete months</p><strong>${report.complete_months} / ${report.months.length}</strong><small>${report.complete ? "Readings and rates covered" : "Review provisional months below"}</small></article><article class="stat-card"><p>Demand & fixed charges</p><strong>${cash(total.demand_cost + total.fixed_cost)}</strong><small>Reset for each monthly bill</small></article><article class="stat-card"><p>Markup</p><strong>${cash(total.markup)}</strong><small>Shown separately from base costs</small></article></div><article class="panel"><div class="panel-heading"><div><p class="eyebrow">MONTHLY COMPARISON</p><h2>Costs over time</h2></div><span class="muted">${escape(report.timezone)}</span></div><div class="monthly-cost-bars">${report.months.map((m) => `<div class="monthly-cost-column" title="${escape(monthLabel(m.month))}: ${cash(m.total_cost)} · ${m.complete ? "Complete" : "Provisional"}"><span>${cash(m.total_cost)}</span><div class="monthly-cost-track"><i style="height:${Math.max(1, (m.total_cost / max) * 100)}%" class="${m.complete ? "" : "provisional"}"></i></div><small>${escape(monthLabel(m.month))}</small></div>`).join("")}</div></article><article class="panel"><div class="panel-heading"><div><p class="eyebrow">MONTHLY BILLING WORKSHEET</p><h2>${report.months.length} months, calculated separately</h2></div><div class="source-actions"><a class="secondary small" href="/api/monthly-report.csv?${query}">↓ Monthly CSV</a><a class="secondary small" href="/api/monthly-report.csv?${query}&detail=meters">↓ Meter details CSV</a></div></div>${!report.complete ? '<p class="info-strip">Provisional months show the priced portion only. Open details to review missing readings, missing rates or charges requiring reconciliation.</p>' : ""}<div class="table-scroll"><table><thead><tr><th>Month / schedule</th><th>Billed kWh</th><th>Energy</th><th>Demand</th><th>Fixed</th><th>Other bills</th><th>Markup</th><th>Total</th><th>Status</th><th></th></tr></thead><tbody>${report.months.map((m) => `<tr><td><strong>${escape(monthLabel(m.month))}</strong><small>${escape(m.selection?.schedule || "Unassigned")} ${m.selection && m.selection.option !== "base" ? escape(m.selection.option) : ""}</small></td><td>${fmt(m.electricity_kwh, 2)}</td><td>${cash(m.energy_cost)}</td><td>${cash(m.demand_cost)}</td><td>${cash(m.fixed_cost)}</td><td>${cash(m.expense_cost)}</td><td>${cash(m.markup)}</td><td><strong>${cash(m.total_cost)}</strong></td><td><span class="tag ${m.complete ? "" : "warning"}">${m.complete ? "Complete" : "Provisional"}</span></td><td><button class="secondary small" data-monthly-detail="${m.month}">Details ↗</button></td></tr>`).join("")}</tbody><tfoot><tr><th>Period total</th><td></td><td>${cash(total.energy_cost)}</td><td>${cash(total.demand_cost)}</td><td>${cash(total.fixed_cost)}</td><td>${cash(total.expense_cost)}</td><td>${cash(total.markup)}</td><td><strong>${cash(total.total_cost)}</strong></td><td colspan="2"></td></tr></tfoot></table></div><p class="form-help">Calendar months use PG&E Pacific time, including daylight-saving changes. Demand and fixed charges apply only to meters configured for account charges with complete data. Mid-month tariff changes requiring demand reconciliation remain provisional. Taxes, special riders and account adjustments can be entered through Bills &amp; allocation. Exports recalculate from saved configuration and retained readings.</p></article>`;
  document
    .querySelectorAll("[data-monthly-detail]")
    .forEach(
      (button) =>
        (button.onclick = () =>
          showMonthlyDetail(button.dataset.monthlyDetail)),
    );
}
function showMonthlyDetail(monthId) {
  const month = monthlyBilling.report?.months.find((m) => m.month === monthId);
  if (!month) return;
  const notes = monthlyNotes(month),
    s = month.selection;
  const versions = (state.status.tariffs || []).filter((t) =>
    month.rate_coverage.tariff_ids.includes(t.id),
  );
  dialog(
    "Monthly bill · " + monthLabel(monthId),
    `<p class="form-help">${escape(month.period_start)} → ${escape(month.period_end)} (exclusive) · ${escape(month.timezone)}<br>${s ? escape(s.schedule + " · " + s.option + " · " + s.voltage + " · " + s.service) : "No rate schedule assigned"} · ${month.rate_coverage.covered_days}/${month.rate_coverage.total_days} days of rates</p>${notes.length ? `<details class="info-strip" open><summary>${month.complete ? "Billing notes" : "Items to review"}</summary><p>${notes.map(escape).join("<br>")}</p></details>` : ""}<div class="table-scroll monthly-detail"><table><thead><tr><th>Meter</th><th>Usage</th><th>Coverage</th><th>Energy</th><th>Demand</th><th>Fixed</th><th>Other bills</th><th>Base cost</th><th>Markup</th><th>Total</th></tr></thead><tbody>${month.meters.map((r) => `<tr><td>${escape(r.name)}<small>${escape(r.category)}${r.electricity ? " · Electric billing" : ""}</small></td><td>${fmt(r.quantity, 3)} ${escape(r.unit)}</td><td>${fmt(r.coverage * 100)}%</td><td>${cash(r.electricity?.energy_cost || 0)}</td><td>${cash(r.electricity?.demand_cost || 0)}</td><td>${cash(r.electricity?.fixed_cost || 0)}</td><td>${cash(r.expense_cost)}</td><td>${cash(r.base_cost)}</td><td>${cash(r.markup)}</td><td>${cash(r.total_cost)}</td></tr>`).join("")}</tbody></table></div><p class="form-help">Retained rate versions:<br>${versions.map((t) => `${escape(t.selection.schedule)} · ${escape(t.effective_from)} → ${escape(t.effective_to || "Present")} · ${/^https:\/\/(?:www\.)?pge\.com\//.test(t.source_url || "") ? `<a class="inline-link" href="${escape(t.source_url)}" target="_blank" rel="noreferrer">Official source ↗</a>` : escape(t.id)}`).join("<br>") || "No applicable imported rate version"}</p>`,
    "monthly-bill-dialog",
  );
}
function showMonthlyImportStatus(job) {
  const box = $("monthly-import-status"),
    running = job?.status === "running";
  $("monthly-fetch").disabled = running;
  if (!job) {
    box.innerHTML = "";
    return;
  }
  const messages = [
    ...(job.errors || []).map(
      (e) => (e.schedule || "") + " " + e.source + ": " + e.error,
    ),
    ...(job.error ? [job.error] : []),
  ];
  const missing =
    job.result?.coverage
      .filter((m) => !m.complete)
      .map((m) => monthLabel(m.month)) || [];
  box.innerHTML = `<div class="info-strip"><strong>Historical rate import · ${escape(job.status)}</strong><p>${job.processed} / ${job.total || "…"} tariff selections · ${job.imported} imported${job.active ? " · " + escape(job.active) : ""}</p>${missing.length ? `<p>Missing rate coverage: ${missing.map(escape).join(", ")}</p>` : ""}${messages.length ? `<details><summary>Import details</summary><p>${messages.map(escape).join("<br>")}</p></details>` : ""}</div>`;
}
async function loadMonthlyImportStatus() {
  clearTimeout(monthlyBilling.timer);
  try {
    const job = await api("/api/rates/history");
    showMonthlyImportStatus(job);
    if (job?.status === "running")
      monthlyBilling.timer = setTimeout(loadMonthlyImportStatus, 1500);
    else if (job) {
      state.status = await api("/api/status");
      renderRates();
      await refreshMonthlyCoverage();
    }
  } catch (e) {
    toast(e.message, true);
  }
}
bindAction("monthly-load", () => {
  monthlyRange();
  renderMonthlyPlan(true);
  invalidateMonthlyReport(
    "Billing range changed. Calculate the selected months.",
  );
});
bindAction("monthly-copy", () => {
  const rows = [...document.querySelectorAll("[data-billing-month]")];
  if (!rows.length) return;
  const first = readMonthlyRow(rows[0]);
  if (!first.schedule)
    throw Error(
      "Enter the first month's rate schedule and service details first",
    );
  for (const row of rows)
    monthlyBilling.drafts.set(row.dataset.billingMonth, { ...first });
  renderMonthlyPlan(true);
  $("monthly-save-status").textContent =
    "Selection copied. Review each month and save.";
  invalidateMonthlyReport(
    "Monthly schedules changed. Save and calculate again.",
  );
});
bindAction("monthly-save", saveMonthlyPlan);
$("monthly-fetch").onclick = (event) =>
  action(event.currentTarget, async () => {
    await saveMonthlyPlan();
    const range = monthlyBilling.range;
    const job = await api("/api/rates/history", {
      method: "POST",
      body: {
        start_month: range.start_month,
        end_month: range.end_month,
        revision: state.revision,
      },
    });
    showMonthlyImportStatus(job);
  })
    .catch(() => {})
    .finally(loadMonthlyImportStatus);
bindAction("monthly-calculate", async () => {
  await saveMonthlyPlan();
  const range = monthlyBilling.range;
  $("monthly-results").innerHTML = empty(
    "Calculating monthly bills…",
    "Each month uses its own schedule, time-of-use prices and recorded readings.",
    "▦",
  );
  const revision = state.revision;
  const report = await api(
    "/api/monthly-report?" +
      new URLSearchParams({
        start_month: range.start_month,
        end_month: range.end_month,
      }),
  );
  if (
    revision !== state.revision ||
    report.configuration_revision !== revision ||
    monthlyDirty()
  ) {
    invalidateMonthlyReport(
      "Configuration changed while bills were calculated. Refresh the workspace and calculate again.",
    );
    return;
  }
  renderMonthlyBills(report);
  $("monthly-results").scrollIntoView({ behavior: "smooth", block: "start" });
});
for (const id of ["monthly-from", "monthly-through"])
  $(id).onchange = () =>
    invalidateMonthlyReport(
      "Click Load months to use the selected billing range.",
    );
