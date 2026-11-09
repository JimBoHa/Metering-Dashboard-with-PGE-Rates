"use strict";
// Extends the existing source workflow; no credentials are stored in browser storage.
const sqlView = {
  sourceId: null,
  inspection: null,
  points: [],
  selected: new Map(),
  query: "",
  next: null,
  generation: 0,
};
function sqlSourceFields(s) {
  return `<label class="full">SQL Server host<input id="sql-host" value="${escape(s.host || "")}" required placeholder="Server DNS name"></label><label>TCP port<input id="sql-port" type="number" min="1" max="65535" value="${s.port || 1433}" required></label><label>Database<input id="sql-database" value="${escape(s.database || "")}" maxlength="128" placeholder="Login default if blank"></label><label>Username<input id="sql-username" value="${escape(s.username || "")}" required autocomplete="off"></label><label>Password<input id="sql-password" type="password" autocomplete="new-password" ${s.password_set ? "" : "required"} placeholder="${s.password_set ? "Saved; leave blank to keep" : "SQL login password"}"></label><label>Connection security<select id="sql-tls">${option("verify", "Encrypted · verify server certificate", s.tls || "verify")}${option("plain", "Legacy trusted LAN · unencrypted", s.tls || "verify")}</select></label><label>Historian layout<select id="sql-profile">${option("auto", "Detect Metasys or choose a mapping", s.profile || "auto")}${option("metasys", "Metasys analog historian", s.profile || "auto")}${option("custom", "Custom tables / views", s.profile || "auto")}</select></label><label class="full">Trusted CA certificate (PEM, optional)<textarea id="sql-ca" rows="3" placeholder="Paste an internal CA certificate if the server does not use a public CA">${escape(s.ca_pem || "")}</textarea></label><label class="full">Timezone for custom naive timestamps<input id="sql-timezone" value="${escape(s.timezone || "UTC")}" required></label><p class="form-help full">Metasys UTCDateTime is always read as UTC. Other schemas can use UTC, a local IANA timezone, Unix seconds or milliseconds. Ambiguous local DST timestamps are rejected.</p><label>Sync interval (seconds)<input id="sql-sync-seconds" type="number" min="60" max="86400" value="${s.sync_seconds || 300}" required></label><label>First sync lookback (days)<input id="sql-initial-days" type="number" min="1" max="3660" value="${s.initial_days || 7}" required></label><label>Recheck recent history (hours)<input id="sql-overlap" type="number" min="1" max="720" value="${s.overlap_hours || 24}" required></label><label>Good quality values (comma separated)<input id="sql-good-values" value="${escape((s.good_quality_values || ["good", "0"]).join(", "))}" required></label><label class="check-row full"><input id="sql-assume-good" type="checkbox" ${s.missing_quality === "good" ? "checked" : ""}> Treat rows without a mapped quality flag as good</label><p class="form-help full">Leave this off until the historian data is verified. Use a SQL login with SELECT access only. This connector issues SELECT queries and never changes remote tables. Saved passwords are encrypted in the dashboard's private state.</p>`;
}
function readSqlSourceFields(previous) {
  return {
    ...previous,
    host: $("sql-host").value.trim(),
    port: Number($("sql-port").value),
    database: $("sql-database").value.trim(),
    username: $("sql-username").value.trim(),
    password: $("sql-password").value,
    tls: $("sql-tls").value,
    ca_pem: $("sql-ca").value.trim(),
    profile: $("sql-profile").value,
    timezone: $("sql-timezone").value.trim(),
    sync_seconds: Number($("sql-sync-seconds").value),
    initial_days: Number($("sql-initial-days").value),
    overlap_hours: Number($("sql-overlap").value),
    good_quality_values: $("sql-good-values")
      .value.split(",")
      .map((v) => v.trim())
      .filter(Boolean),
    missing_quality: $("sql-assume-good").checked ? "good" : "unverified",
  };
}
async function openSqlSource(sourceId) {
  const source = state.config.sources.find((s) => s.id === sourceId);
  if (!source) throw Error("Source not found");
  if (sqlView.sourceId !== sourceId) {
    sqlView.selected.clear();
    sqlView.points = [];
    sqlView.query = "";
  }
  const generation = ++sqlView.generation;
  sqlView.sourceId = sourceId;
  $("sql-workspace").hidden = false;
  $("sql-workspace").innerHTML = empty(
    "Connecting to SQL Server…",
    "Inspecting the selected database with read-only queries.",
    "⌁",
  );
  $("sql-workspace").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const inspection = await api(
      "/api/sql/" + encodeURIComponent(sourceId) + "/inspect",
    );
    if (generation !== sqlView.generation) return;
    sqlView.inspection = inspection;
    $("sql-workspace").innerHTML =
      `<div class="panel-heading"><div><p class="eyebrow">REMOTE TREND DATA</p><h2>${escape(source.name)}</h2></div><span class="tag">${inspection.profile === "metasys" ? "Metasys analog history" : "Custom SQL history"}</span></div><div class="sql-connection-row"><label>Database<select id="sql-db-picker">${[...new Set([inspection.database, ...inspection.databases])].map((n) => option(n, n, inspection.database)).join("")}</select></label><button id="sql-reconnect" class="secondary small">↻ Reconnect</button><button id="sql-import-existing" class="secondary small">Import existing meters</button></div><p class="form-help">${source.settings.missing_quality === "good" ? "Rows without a quality flag are accepted as good by your source setting." : "Rows without a quality flag remain unverified and are excluded from billing."}</p><details id="sql-map-details" ${inspection.ready ? "" : "open"}><summary>Table and column mapping${inspection.profile === "metasys" ? " · optional override" : ""}</summary><div id="sql-mapping" style="margin-top:18px"></div></details><div id="sql-browse" ${inspection.ready ? "" : "hidden"}><div class="toolbar" style="margin-top:24px"><input id="sql-point-query" type="search" placeholder="Search the database by point name or path…" aria-label="Search SQL points"><div><button id="sql-search" class="secondary small">Search</button><button id="sql-create-selected" class="primary small">Create meters (0)</button></div></div><p id="sql-tree-count" class="form-help"></p><div id="sql-point-tree" class="sql-point-tree"></div><button id="sql-more-points" class="secondary small" style="margin-top:15px" hidden>Load more points</button></div>`;
    $("sql-db-picker").onchange = () =>
      action($("sql-db-picker"), async () => {
        const cfg = copyConfig();
        cfg.sources.find((s) => s.id === sourceId).settings.database =
          $("sql-db-picker").value;
        await saveConfig(cfg);
        sqlView.selected.clear();
        await openSqlSource(sourceId);
      }).catch(() => {});
    $("sql-reconnect").onclick = () =>
      action($("sql-reconnect"), () => openSqlSource(sourceId)).catch(() => {});
    $("sql-import-existing").onclick = () => openSqlImport(sourceId);
    renderSqlMapping(source, inspection);
    $("sql-search").onclick = () =>
      action($("sql-search"), () => loadSqlPoints(false)).catch(() => {});
    $("sql-point-query").onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        action($("sql-search"), () => loadSqlPoints(false)).catch(() => {});
      }
    };
    $("sql-more-points").onclick = () =>
      action($("sql-more-points"), () => loadSqlPoints(true)).catch(() => {});
    $("sql-create-selected").onclick = () => reviewSqlMeters();
    if (inspection.ready) await loadSqlPoints(false);
  } catch (e) {
    $("sql-workspace").innerHTML = empty(
      "SQL connection needs attention",
      e.message,
      "⌁",
    );
    throw e;
  }
}
function renderSqlMapping(source, inspection) {
  const tables = inspection.tables,
    m = source.settings.mapping || inspection.suggested_mapping || {};
  const key = (t) => JSON.stringify([t.schema, t.name]);
  const chosen =
    tables.find((t) => t.schema === m.schema_name && t.name === m.table) ||
    tables[0];
  const meta = tables.find(
    (t) => t.schema === m.point_schema && t.name === m.point_table,
  );
  $("sql-mapping").innerHTML =
    `<form id="sql-mapping-form" class="form-grid"><label class="full">Trend table / view<select id="sql-trend-table" required>${tables.map((t) => option(key(t), t.schema + "." + t.name, chosen ? key(chosen) : "")).join("")}</select></label><div id="sql-trend-columns" class="form-grid full"></div><label class="full">Point metadata table / view (optional)<select id="sql-meta-table">${option("", "Use point IDs from the trend table", meta ? key(meta) : "")}${tables.map((t) => option(key(t), t.schema + "." + t.name, meta ? key(meta) : "")).join("")}</select></label><div id="sql-meta-columns" class="form-grid full"></div><p class="form-help full">The metadata key joins to the trend point ID. A path column builds the point tree; without metadata, the tree uses the distinct point IDs. Queries are generated from the selected fields.</p><p id="sql-map-error" class="dialog-error"></p><button class="secondary full">Save mapping & browse points</button></form>`;
  function table(id) {
    const value = $(id).value;
    return value ? tables.find((t) => key(t) === value) : null;
  }
  function select(id, label, cols, selected, optional = false) {
    return `<label>${label}<select id="${id}" ${optional ? "" : "required"}>${option("", "Select " + (optional ? "(optional)" : "column"), selected || "")}${cols.map((c) => option(c.name, c.name + " · " + c.type, selected)).join("")}</select></label>`;
  }
  const numeric = new Set([
    "tinyint",
    "smallint",
    "int",
    "bigint",
    "decimal",
    "numeric",
    "float",
    "real",
    "money",
    "smallmoney",
  ]);
  const dates = new Set([
    "date",
    "datetime",
    "datetime2",
    "smalldatetime",
    "datetimeoffset",
  ]);
  function trendFields() {
    const t = table("sql-trend-table");
    if (!t) return;
    const same = t.schema === m.schema_name && t.name === m.table;
    const guess = (names) =>
      t.columns.find((c) => names.includes(c.name.toLowerCase()))?.name;
    $("sql-trend-columns").innerHTML =
      select(
        "sql-map-point",
        "Point ID column",
        t.columns,
        same
          ? m.point_id_column
          : guess(["pointid", "point_id", "meterid", "meter_id", "tagid"]),
      ) +
      select(
        "sql-map-time",
        "Timestamp column",
        t.columns.filter((c) => numeric.has(c.type) || dates.has(c.type)),
        same
          ? m.timestamp_column
          : guess(["utcdatetime", "timestamp", "sample_time", "recorded_at"]),
      ) +
      select(
        "sql-map-value",
        "Numeric value column",
        t.columns.filter((c) => numeric.has(c.type)),
        same
          ? m.value_column
          : guess(["actualvalue", "value", "sample_value", "reading"]),
      ) +
      select(
        "sql-map-quality",
        "Quality column",
        t.columns,
        same ? m.quality_column : "",
        true,
      ) +
      `<label>Timestamp format<select id="sql-map-kind">${option("datetime", "SQL date/time", m.timestamp_kind || "datetime")}${option("unix_seconds", "Unix seconds", m.timestamp_kind || "datetime")}${option("unix_milliseconds", "Unix milliseconds", m.timestamp_kind || "datetime")}</select></label>`;
  }
  function metaFields() {
    const t = table("sql-meta-table");
    if (!t) {
      $("sql-meta-columns").innerHTML = "";
      return;
    }
    const same = t.schema === m.point_schema && t.name === m.point_table;
    const guess = (names) =>
      t.columns.find((c) => names.includes(c.name.toLowerCase()))?.name;
    $("sql-meta-columns").innerHTML =
      select(
        "sql-map-key",
        "Point key column",
        t.columns,
        same ? m.point_key_column : guess(["pointid", "point_id", "id"]),
      ) +
      select(
        "sql-map-name",
        "Point name column",
        t.columns,
        same ? m.point_name_column : guess(["pointname", "point_name", "name"]),
      ) +
      select(
        "sql-map-path",
        "Tree path column",
        t.columns,
        same ? m.point_path_column : guess(["path", "pointpath", "point_path"]),
        true,
      ) +
      select(
        "sql-map-unit",
        "Unit column",
        t.columns,
        same ? m.point_unit_column : guess(["units", "unit"]),
        true,
      );
  }
  trendFields();
  metaFields();
  $("sql-trend-table").onchange = trendFields;
  $("sql-meta-table").onchange = metaFields;
  $("sql-mapping-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await action(e.submitter, async () => {
        const t = table("sql-trend-table"),
          p = table("sql-meta-table");
        const mapping = {
          schema_name: t.schema,
          table: t.name,
          point_id_column: $("sql-map-point").value,
          timestamp_column: $("sql-map-time").value,
          value_column: $("sql-map-value").value,
          timestamp_kind: $("sql-map-kind").value,
          quality_column: $("sql-map-quality").value || null,
          point_schema: p?.schema || "dbo",
          point_table: p?.name || null,
          point_key_column: p ? $("sql-map-key").value : null,
          point_name_column: p ? $("sql-map-name").value : null,
          point_path_column: p ? $("sql-map-path").value || null : null,
          point_unit_column: p ? $("sql-map-unit").value || null : null,
        };
        const cfg = copyConfig(),
          s = cfg.sources.find((s) => s.id === source.id);
        s.settings.profile = "custom";
        s.settings.mapping = mapping;
        await saveConfig(cfg);
        sqlView.selected.clear();
        await openSqlSource(source.id);
        toast("SQL mapping saved");
      });
    } catch (e) {
      if ($("sql-map-error")) $("sql-map-error").textContent = e.message;
    }
  };
}
async function loadSqlPoints(append) {
  const sourceId = sqlView.sourceId,
    generation = sqlView.generation;
  const query = append ? sqlView.query : $("sql-point-query").value.trim();
  const params = new URLSearchParams({
    q: query,
    offset: String(append ? sqlView.next || 0 : 0),
    limit: "500",
  });
  $("sql-tree-count").textContent = "Reading point catalog…";
  const data = await api(
    "/api/sql/" + encodeURIComponent(sourceId) + "/points?" + params,
  );
  if (sourceId !== sqlView.sourceId || generation !== sqlView.generation)
    return;
  sqlView.query = query;
  sqlView.next = data.next_offset;
  const previous = append ? sqlView.points : [];
  sqlView.points = [
    ...new Map([...previous, ...data.points].map((p) => [p.id, p])).values(),
  ];
  renderSqlTree();
}
function renderSqlTree() {
  const root = { children: new Map(), leaves: [] };
  sqlView.points.forEach((p, i) => {
    const parts = (p.path || p.name || p.id).split(/[\\/.:]+/).filter(Boolean);
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.children.has(part))
        node.children.set(part, { children: new Map(), leaves: [] });
      node = node.children.get(part);
    }
    node.leaves.push({ p, i, label: parts.at(-1) || p.name });
  });
  const draw = (node, depth = 0) =>
    `<ul>${[...node.children]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(
        ([label, child]) =>
          `<li><details ${depth < 1 ? "open" : ""}><summary><span class="sql-folder-icon">▱</span> ${escape(label)}</summary>${draw(child, depth + 1)}</details></li>`,
      )
      .join(
        "",
      )}${node.leaves.map(({ p, i, label }) => `<li class="sql-point-leaf"><label title="${escape(p.path)}"><input type="checkbox" data-sql-point="${i}" ${sqlView.selected.has(p.id) ? "checked" : ""}><span>${escape(label)}<small>${escape(p.units || "Unit not supplied")} · ID ${escape(p.id)}</small></span></label></li>`).join("")}</ul>`;
  $("sql-point-tree").innerHTML = sqlView.points.length
    ? `<details open><summary><strong>${escape(sqlView.inspection.database)}</strong></summary>${draw(root)}</details>`
    : empty(
        "No matching trend points",
        "Try another name/path filter or review the table mapping.",
        "⌁",
      );
  $("sql-tree-count").textContent =
    `${sqlView.points.length.toLocaleString()} loaded points${sqlView.next !== null ? " · more available" : ""}. Search checks the whole database. Expand folders to choose points.`;
  $("sql-more-points").hidden = sqlView.next === null;
  document.querySelectorAll("[data-sql-point]").forEach(
    (el) =>
      (el.onchange = () => {
        const p = sqlView.points[+el.dataset.sqlPoint];
        if (el.checked) sqlView.selected.set(p.id, p);
        else sqlView.selected.delete(p.id);
        $("sql-create-selected").textContent =
          `Create meters (${sqlView.selected.size})`;
      }),
  );
  $("sql-create-selected").textContent =
    `Create meters (${sqlView.selected.size})`;
}
function sqlUnits(value) {
  const found = mapUnit(value);
  if (found[0]) return found;
  return (
    {
      kwh: ["kWh", "counter", 3600],
      kw: ["kWh", "rate", 3600],
      wh: ["kWh", "counter", 3600, 0.001],
      w: ["kWh", "rate", 3600, 0.001],
      gal: ["gal", "counter", 3600],
      gpm: ["gal", "rate", 60],
      m3: ["m3", "counter", 3600],
      l: ["L", "counter", 3600],
      therm: ["therm", "counter", 3600],
      mmbtu: ["MMBtu", "counter", 3600],
      kbtu: ["kBtu", "counter", 3600],
    }[String(value).toLowerCase()] || ["", "counter", 3600]
  );
}
function sqlDates() {
  const end = new Date();
  end.setDate(end.getDate() + 1);
  const start = new Date();
  start.setDate(start.getDate() - 7);
  return { start: dateStr(start), end: dateStr(end) };
}
function reviewSqlMeters() {
  const points = [...sqlView.selected.values()];
  if (!points.length) {
    toast("Choose points in the SQL tree first", true);
    return;
  }
  if (points.length > 50) {
    toast("Create at most 50 SQL meters at once", true);
    return;
  }
  const sourceId = sqlView.sourceId,
    dates = sqlDates();
  dialog(
    "Create meters from SQL trends",
    `<form id="sql-meter-review-form" class="form-grid"><p class="form-help full">Confirm each point's quantity unit and reading type. Historical data keeps its original timestamps. Cumulative counters, instantaneous rates and interval quantities have different billing semantics.</p><label>Backfill starts<input id="sql-create-start" type="date" value="${dates.start}" required></label><label>Backfill ends (exclusive)<input id="sql-create-end" type="date" value="${dates.end}" required></label><label class="full">Maximum gap between usable samples (seconds)<input id="sql-create-gap" type="number" min="1" max="86400" value="3600" required></label><div class="full sql-review-list">${points
      .map((p, i) => {
        const u = sqlUnits(p.units);
        const title = p.path.split(/[\\/]/).at(-1) || p.name;
        return `<article class="sql-review-item"><label>Meter name<input data-sql-name="${i}" value="${escape(title.slice(0, 120))}" required maxlength="120"></label><p class="form-help">${escape(p.path)} · source unit: ${escape(p.units || "not supplied")}</p><div class="form-grid"><label>Quantity unit<select data-sql-unit="${i}" required>${option("", "Choose unit", u[0])}${units.map((v) => option(v, v, u[0])).join("")}</select></label><label>Reading type<select data-sql-mode="${i}">${[
          ["counter", "Cumulative counter"],
          ["rate", "Instantaneous rate"],
          ["interval", "Interval quantity"],
        ]
          .map(([v, l]) => option(v, l, u[1]))
          .join(
            "",
          )}</select></label><label>System<select data-sql-category="${i}">${categories.map((c) => option(c, c, u[0] === "kWh" ? "Electrical" : "Other")).join("")}</select></label><label>Scale multiplier<input data-sql-scale="${i}" type="number" step="any" value="${u[3] || 1}" required></label><label data-sql-basis-label="${i}">${u[1] === "interval" ? "Interval duration" : "Rate basis"} (seconds)<input data-sql-basis="${i}" type="number" min="1" max="86400" value="${u[1] === "interval" ? 900 : u[2]}" ${u[1] === "counter" ? "disabled" : ""}></label></div></article>`;
      })
      .join(
        "",
      )}</div><p class="form-help full">End dates are exclusive; a future end is capped at the current time. Source quality settings determine whether imported samples can contribute to billing. Electricity pricing remains off until you enable it on the intended meters.</p><p id="editor-error" class="dialog-error"></p><div class="dialog-actions"><button class="primary">Create meters & import history</button></div></form>`,
  );
  document.querySelectorAll("[data-sql-mode]").forEach(
    (el) =>
      (el.onchange = () => {
        const i = el.dataset.sqlMode,
          basis = document.querySelector(`[data-sql-basis="${i}"]`);
        basis.disabled = el.value === "counter";
        basis.value =
          el.value === "interval" ? 900 : sqlUnits(points[+i].units)[2];
      }),
  );
  $("sql-meter-review-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await action(e.submitter, async () => {
        const meters = points.map((p, i) => {
          const mode = document.querySelector(`[data-sql-mode="${i}"]`).value,
            basis = Number(
              document.querySelector(`[data-sql-basis="${i}"]`).value,
            );
          return {
            ...defaultMeter(),
            id: uid("sql-meter"),
            name: document.querySelector(`[data-sql-name="${i}"]`).value.trim(),
            category: document.querySelector(`[data-sql-category="${i}"]`)
              .value,
            unit: document.querySelector(`[data-sql-unit="${i}"]`).value,
            mode,
            source_id: sourceId,
            point: p.point,
            scale: Number(
              document.querySelector(`[data-sql-scale="${i}"]`).value,
            ),
            max_gap_seconds: Number($("sql-create-gap").value),
            rate_period_seconds: mode === "rate" ? basis : 3600,
            interval_seconds: mode === "interval" ? basis : 900,
            billable: false,
            x: 60 + ((state.config.meters.length + i) % 5) * 290,
            y: 60 + Math.floor((state.config.meters.length + i) / 5) * 230,
          };
        });
        const result = await api("/api/sql/meters", {
          method: "POST",
          body: {
            source_id: sourceId,
            revision: state.revision,
            meters,
            start: $("sql-create-start").value,
            end: $("sql-create-end").value,
          },
        });
        state.config = result.configuration;
        state.revision = result.revision;
        sqlView.selected.clear();
        $("editor").close();
        await load();
        renderSqlTree();
        toast(result.message);
      });
    } catch (e) {
      formError(e);
    }
  };
}
function openSqlImport(sourceId) {
  const source = state.config.sources.find((s) => s.id === sourceId),
    meters = state.config.meters.filter(
      (m) => m.source_id === sourceId && m.enabled,
    );
  if (!meters.length) {
    toast("Create meters from this source’s point tree first", true);
    return;
  }
  const dates = sqlDates();
  dialog(
    "Import SQL trend history",
    `<form id="sql-import-form" class="form-grid"><p class="form-help full">${escape(source.name)}. Select a historical range or sync from the saved checkpoint with the configured overlap.</p><label class="full">Import mode<select id="sql-import-mode"><option value="range">Historical backfill</option><option value="sync">Incremental sync now</option></select></label><label id="sql-import-start-label">From<input id="sql-import-start" type="date" value="${dates.start}" required></label><label id="sql-import-end-label">To (exclusive)<input id="sql-import-end" type="date" value="${dates.end}" required></label><div class="full"><label class="check-row"><input id="sql-select-all-meters" type="checkbox" checked> Select all enabled SQL meters</label><div style="margin-top:12px">${meters.map((m) => `<label class="check-row" style="padding:7px 0"><input type="checkbox" data-sql-import-meter="${m.id}" checked>${escape(m.name)} · ${escape(m.unit)}</label>`).join("")}</div></div><p class="form-help full">Repeated imports skip identical readings. Conflicting values stop the job without overwriting existing local readings. Imports can be cancelled or resumed from their last saved page.</p><p id="editor-error" class="dialog-error"></p><div class="dialog-actions"><button class="primary">Start import</button></div></form>`,
  );
  $("sql-import-mode").onchange = () => {
    const hidden = $("sql-import-mode").value === "sync";
    $("sql-import-start-label").hidden = hidden;
    $("sql-import-end-label").hidden = hidden;
  };
  $("sql-select-all-meters").onchange = (e) =>
    document
      .querySelectorAll("[data-sql-import-meter]")
      .forEach((el) => (el.checked = e.target.checked));
  $("sql-import-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await action(e.submitter, async () => {
        const meter_ids = [
          ...document.querySelectorAll("[data-sql-import-meter]:checked"),
        ].map((el) => el.dataset.sqlImportMeter);
        if (!meter_ids.length) throw Error("Choose at least one meter");
        const body = { source_id: sourceId, meter_ids };
        if ($("sql-import-mode").value === "range") {
          body.start = $("sql-import-start").value;
          body.end = $("sql-import-end").value;
        }
        await api("/api/sql/imports", { method: "POST", body });
        $("editor").close();
        state.status = await api("/api/status");
        renderSqlJobs();
        toast("SQL import started");
      });
    } catch (e) {
      formError(e);
    }
  };
}
function renderSqlJobs() {
  const jobs = state.status.sql_jobs || [];
  $("sql-job-panel").hidden = !jobs.length;
  $("sql-job-list").innerHTML = jobs
    .slice(0, 12)
    .map((job) => {
      const source = state.config.sources.find((s) => s.id === job.source_id),
        active = ["queued", "running"].includes(job.status),
        resumable = ["failed", "cancelled", "interrupted"].includes(job.status);
      const current = state.config.meters.find(
        (m) => m.id === job.current_meter,
      )?.name;
      return `<div class="sql-job"><div class="source-row"><div><h3>${escape(source?.name || "Removed SQL source")} <span class="tag ${job.status === "failed" ? "error" : active ? "warning" : ""}">${escape(job.status)}</span></h3><p class="muted">${job.automatic ? "Scheduled sync" : "Requested import"} · ${job.completed_meters}/${job.meter_ids.length} meters · ${fmt(job.inserted, 0)} new · ${fmt(job.duplicates, 0)} duplicates · ${fmt(job.quality_updates || 0, 0)} quality updates</p>${current ? `<p class="form-help">Current meter: ${escape(current)}</p>` : ""}${job.error ? `<p class="sql-job-error">${escape(job.error)}</p>` : ""}</div><div class="source-actions">${active ? `<button class="secondary small" data-sql-cancel="${job.id}" ${job.cancel_requested ? "disabled" : ""}>${job.cancel_requested ? "Cancelling…" : "Cancel"}</button>` : ""}${resumable ? `<button class="secondary small" data-sql-resume="${job.id}">Resume</button>` : ""}</div></div></div>`;
    })
    .join("");
  document.querySelectorAll("[data-sql-cancel]").forEach(
    (b) =>
      (b.onclick = () =>
        action(b, async () => {
          await api("/api/sql/jobs/" + b.dataset.sqlCancel + "/cancel", {
            method: "POST",
          });
          state.status = await api("/api/status");
          renderSqlJobs();
        }).catch(() => {})),
  );
  document.querySelectorAll("[data-sql-resume]").forEach(
    (b) =>
      (b.onclick = () =>
        action(b, async () => {
          await api("/api/sql/jobs/" + b.dataset.sqlResume + "/resume", {
            method: "POST",
          });
          state.status = await api("/api/status");
          renderSqlJobs();
        }).catch(() => {})),
  );
}
setInterval(async () => {
  if (!state.csrf || state.page !== "discovery" || document.hidden) return;
  try {
    state.status = await api("/api/status");
    renderSqlJobs();
  } catch {}
}, 4000);
