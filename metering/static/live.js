"use strict";

function liveNumber(value, digits = 2) {
  return typeof value === "number" && Number.isFinite(value)
    ? fmt(value, digits)
    : "—";
}
function liveTime(timestamp) {
  return timestamp
    ? new Date(timestamp * 1000).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: state.live?.timezone || state.config.timezone,
      })
    : "No samples";
}
function meterInputLabel(meter) {
  const live = state.live?.readings?.[meter.id];
  return (
    meter.name +
    (live?.unit && live.unit !== meter.unit
      ? ` · live ${live.unit}, usage ${meter.unit}`
      : ` · ${meter.unit}${meter.mode === "counter" ? " counter" : ""}`)
  );
}
function liveMeterMarkup(m, compact = false) {
  const reading = state.live?.readings?.[m.id],
    month = state.live?.month_to_date?.[m.id];
  const monthly = m.display_on_dashboard === "month_to_date";
  const value = monthly ? month?.quantity : reading?.value;
  const unit = monthly ? m.unit : reading?.unit || m.unit;
  const label = monthly
    ? "RECORDED THIS MONTH"
    : reading?.kind === "rate"
      ? "LIVE RATE"
      : "LIVE REGISTER";
  const missing = reading?.value == null;
  const quality = monthly
    ? !month || month.provisional
      ? "Provisional"
      : "Observed"
    : missing
      ? reading?.required_inputs
        ? `${reading.available_inputs}/${reading.required_inputs} inputs available`
        : reading?.quality || "Awaiting data"
      : reading.quality === "good"
        ? "Live"
        : "Unverified";
  const notes = [...(reading?.issues || []), ...(month?.issues || [])];
  let secondary = monthly
    ? `${reading?.kind === "rate" ? "Latest rate" : "Register"}: ${liveNumber(reading?.value)} ${escape(reading?.unit || m.unit)}`
    : month
      ? `${reading?.kind === "rate" ? "Integrated this month" : "This month"}: ${liveNumber(month.quantity)} ${escape(m.unit)}`
      : "";
  if (missing && reading?.partial_value != null)
    secondary = `Available subtotal: ${liveNumber(reading.partial_value)} ${escape(unit)}`;
  const coverage = month
    ? `Observed coverage ${fmt(month.coverage * 100, 2)}%${month.first_interval ? (compact ? "" : " · since " + escape(liveTime(month.first_interval))) : " · waiting for overlapping samples"}`
    : "";
  return `<div class="live-value-label">${label}</div><div class="${compact ? "node-quantity" : "meter-value"}">${liveNumber(value)} <small>${escape(unit)}</small></div>${secondary ? `<div class="live-secondary">${secondary}</div>` : ""}${coverage ? `<div class="live-coverage">${coverage}</div>` : ""}<div class="live-quality" title="${escape(notes.join("; "))}"><span class="tag ${quality === "Live" || quality === "Observed" ? "" : "warning"}">${escape(quality)}</span><span>${escape(liveTime(monthly ? month?.last_interval : reading?.timestamp))}</span></div>`;
}
function renderLiveSummary() {
  const panel = $("live-summary");
  if (!state.config) return;
  const pinned = state.config.meters.filter(
    (m) => m.display_on_dashboard && m.display_on_dashboard !== "none",
  );
  panel.hidden = !pinned.length || !["overview", "canvas"].includes(state.page);
  if (panel.hidden) return;
  panel.innerHTML = pinned
    .map(
      (m) =>
        `<article class="panel live-tile"><div class="panel-heading"><h2>${escape(m.name)}</h2><button class="secondary small" data-live-edit="${m.id}">Edit ↗</button></div>${liveMeterMarkup(m)}<p class="form-help">${m.display_on_dashboard === "month_to_date" ? "Observed usage since the start of this month; incomplete and unverified history stays provisional." : "Newest available readings from every signed input. A missing or stale input leaves the full total unavailable."}</p></article>`,
    )
    .join("");
  panel
    .querySelectorAll("[data-live-edit]")
    .forEach(
      (button) => (button.onclick = () => editMeter(button.dataset.liveEdit)),
    );
}
function canvasMeters() {
  const filter = $("canvas-filter"),
    pinned = state.config.meters.filter(
      (m) => m.display_on_dashboard && m.display_on_dashboard !== "none",
    );
  if (!filter.dataset.initialized) {
    filter.value = pinned.length ? "featured" : "all";
    filter.dataset.initialized = "true";
  }
  if (filter.value === "all" || !pinned.length) return state.config.meters;
  const byid = new Map(state.config.meters.map((m) => [m.id, m])),
    included = new Set();
  function include(id) {
    if (included.has(id)) return;
    included.add(id);
    for (const term of byid.get(id)?.terms || [])
      if (term.factor) include(term.meter_id);
  }
  pinned.forEach((m) => include(m.id));
  return state.config.meters.filter((m) => included.has(m.id));
}
function updateCanvasLive() {
  if (state.page !== "canvas") return;
  const meters = new Map(state.config.meters.map((m) => [m.id, m]));
  document.querySelectorAll("[data-live-node]").forEach((body) => {
    const m = meters.get(body.dataset.liveNode);
    if (m)
      body.innerHTML = `<strong>${escape(m.name)}</strong>${liveMeterMarkup(m, true)}`;
  });
}
async function refreshLiveReadings() {
  if (
    !state.csrf ||
    document.hidden ||
    !["overview", "canvas", "meters"].includes(state.page)
  )
    return;
  try {
    state.live = await api("/api/live");
    renderLiveSummary();
    updateCanvasLive();
    if (state.page === "meters") renderMeters();
  } catch (error) {
    toast(error.message, true);
  }
}
function renderDemandRates(tariff) {
  return `<p class="eyebrow" style="margin-top:16px">DEMAND RATES</p>${(tariff.demand || []).map((d) => `<div class="rate-cell"><span>${escape(d.season)} / ${escape(d.period.replaceAll("_", " "))}</span><strong>$${d.price.toFixed(5)} / kW${d.basis === "daily" ? " / day" : " per billing month"}</strong></div>`).join("") || '<p class="form-help">No demand charges in this imported schedule.</p>'}`;
}
$("canvas-filter").onchange = renderCanvas;
setInterval(refreshLiveReadings, 15000);
