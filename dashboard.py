"""Single-page dashboard HTML.

Kept as a Python string so the whole app ships as a handful of .py files with no
template/static-dir wiring for Connect to trip over. Palette is the validated
data-viz reference instance (dark categorical + status slots); Plotly reads the
CSS custom properties at render time so the light/dark toggle has one source of
truth. All fetch() URLs are RELATIVE (no leading slash) so they resolve under
Connect's /content/<guid>/ path prefix.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Connect OTel Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
<style>
  :root {
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
    --series-4: #c98500; --series-5: #d55181;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b;
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
    --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
    --series-4: #eda100; --series-5: #e87ba4;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--text-primary);
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  header { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
           border-bottom: 1px solid var(--border); }
  header h1 { font-size: 18px; margin: 0; font-weight: 600; }
  header .sub { color: var(--muted); font-size: 13px; }
  header .spacer { flex: 1; }
  button.toggle { background: var(--surface-1); color: var(--text-secondary);
    border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px;
    cursor: pointer; font-size: 13px; }
  main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
    gap: 12px; margin-bottom: 20px; }
  .tile { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 16px; }
  .tile .label { color: var(--muted); font-size: 12px; text-transform: uppercase;
    letter-spacing: .04em; }
  .tile .value { font-size: 28px; font-weight: 600; margin-top: 4px; }
  .tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
  .tab { padding: 10px 16px; cursor: pointer; color: var(--text-secondary);
    border-bottom: 2px solid transparent; font-size: 14px; }
  .tab.active { color: var(--text-primary); border-bottom-color: var(--series-1); }
  .panel { display: none; } .panel.active { display: block; }
  .card { background: var(--surface-1); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px; margin-bottom: 16px; }
  .card h2 { font-size: 14px; margin: 0 0 12px; font-weight: 600; }
  select { background: var(--surface-1); color: var(--text-primary);
    border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px;
    font-size: 13px; margin-bottom: 12px; min-width: 320px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid);
    font-variant-numeric: tabular-nums; white-space: nowrap; }
  th { color: var(--muted); font-weight: 600; text-transform: uppercase;
    font-size: 11px; letter-spacing: .04em; }
  td.body { white-space: normal; max-width: 640px; }
  .num { text-align: right; }
  .sev { font-weight: 600; }
  .sev-error, .sev-fatal { color: var(--critical); }
  .sev-warn { color: var(--warning); }
  .sev-info { color: var(--series-1); }
  .sev-debug, .sev-trace { color: var(--muted); }
  .st-ERROR { color: var(--critical); font-weight: 600; }
  .st-OK { color: var(--good); }
  .st-UNSET { color: var(--muted); }
  .empty { color: var(--muted); padding: 24px; text-align: center; }
  code { color: var(--text-secondary); }
</style>
</head>
<body>
<header>
  <h1>Posit Connect — OpenTelemetry</h1>
  <span class="sub" id="lastseen">waiting for signals…</span>
  <span class="spacer"></span>
  <button class="toggle" id="themeBtn" onclick="toggleTheme()">☀ Light</button>
</header>
<main>
  <div class="tiles" id="tiles"></div>

  <div class="tabs">
    <div class="tab active" data-tab="metrics" onclick="selectTab('metrics')">Metrics</div>
    <div class="tab" data-tab="logs" onclick="selectTab('logs')">Logs</div>
    <div class="tab" data-tab="traces" onclick="selectTab('traces')">Traces</div>
  </div>

  <div class="panel active" id="panel-metrics">
    <div class="card">
      <h2>Metric explorer</h2>
      <select id="metricSelect" onchange="drawMetric()"></select>
      <div id="metricChart" style="height:360px"></div>
    </div>
  </div>

  <div class="panel" id="panel-logs">
    <div class="card"><h2>Log records by severity</h2><div id="sevChart" style="height:240px"></div></div>
    <div class="card"><h2>Recent logs</h2><div id="logsTable"></div></div>
  </div>

  <div class="panel" id="panel-traces">
    <div class="card"><h2>Slowest spans (ms)</h2><div id="slowChart" style="height:320px"></div></div>
    <div class="card"><h2>Span performance by name</h2><div id="spanStats"></div></div>
    <div class="card"><h2>Recent spans</h2><div id="spansTable"></div></div>
  </div>
</main>

<script>
let currentTab = "metrics";

function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

function layout(extra) {
  const base = {
    paper_bgcolor: cssVar("--surface-1"), plot_bgcolor: cssVar("--surface-1"),
    font: { color: cssVar("--text-secondary"), family: "system-ui, sans-serif", size: 12 },
    margin: { l: 60, r: 20, t: 10, b: 40 },
    xaxis: { gridcolor: cssVar("--grid"), zerolinecolor: cssVar("--baseline"), linecolor: cssVar("--baseline") },
    yaxis: { gridcolor: cssVar("--grid"), zerolinecolor: cssVar("--baseline"), linecolor: cssVar("--baseline") },
    showlegend: false, hovermode: "closest",
  };
  return Object.assign(base, extra || {});
}
const PLOT_CFG = { displayModeBar: false, responsive: true };

async function j(url) { const r = await fetch(url); return r.ok ? r.json() : []; }

function fmtNum(n) { return (n === null || n === undefined) ? "—" : Number(n).toLocaleString(); }

async function refreshSummary() {
  const s = await j("api/summary");
  const tiles = [
    ["Services", s.services], ["Nodes", s.nodes],
    ["Metric points", s.metric_points], ["Metric names", s.metric_names],
    ["Log records", s.log_records], ["Traces", s.traces], ["Spans", s.spans],
  ];
  document.getElementById("tiles").innerHTML = tiles.map(([l,v]) =>
    `<div class="tile"><div class="label">${l}</div><div class="value">${fmtNum(v)}</div></div>`).join("");
  document.getElementById("lastseen").textContent =
    s.last_seen ? ("last signal " + new Date(s.last_seen).toLocaleString()) : "no signals received yet";
}

async function loadMetricNames() {
  const names = await j("api/metrics/names");
  const sel = document.getElementById("metricSelect");
  const prev = sel.value;
  sel.innerHTML = names.map(m =>
    `<option value="${m.metric_name}">${m.metric_name} — ${m.metric_type}${m.unit ? " ("+m.unit+")" : ""}</option>`).join("");
  if (names.length === 0) { sel.innerHTML = '<option value="">no metrics yet</option>'; }
  if (prev) sel.value = prev;
  drawMetric();
}

async function drawMetric() {
  const name = document.getElementById("metricSelect").value;
  const el = document.getElementById("metricChart");
  if (!name) { el.innerHTML = '<div class="empty">No metric data received yet.</div>'; return; }
  const rows = await j("api/metrics/timeseries?name=" + encodeURIComponent(name) + "&minutes=180");
  const trace = {
    x: rows.map(r => r.bucket), y: rows.map(r => r.value),
    type: "scatter", mode: "lines", line: { color: cssVar("--series-1"), width: 2 },
    fill: "tozeroy", fillcolor: "rgba(57,135,229,0.12)", name: name,
  };
  Plotly.react(el, [trace], layout({ yaxis: { gridcolor: cssVar("--grid"), title: name } }), PLOT_CFG);
}

function sevColor(s) {
  s = (s || "").toUpperCase();
  if (s.startsWith("ERROR") || s.startsWith("FATAL")) return cssVar("--critical");
  if (s.startsWith("WARN")) return cssVar("--warning");
  if (s.startsWith("INFO")) return cssVar("--series-1");
  return cssVar("--muted");
}
function sevClass(s) {
  s = (s || "").toLowerCase();
  if (s.startsWith("error")||s.startsWith("fatal")) return "sev-error";
  if (s.startsWith("warn")) return "sev-warn";
  if (s.startsWith("info")) return "sev-info";
  return "sev-debug";
}

async function loadLogs() {
  const sev = await j("api/logs/severity");
  const sel = document.getElementById("sevChart");
  if (sev.length === 0) { sel.innerHTML = '<div class="empty">No logs received yet.</div>'; }
  else {
    Plotly.react(sel, [{
      x: sev.map(r => r.n), y: sev.map(r => r.severity_text),
      type: "bar", orientation: "h",
      marker: { color: sev.map(r => sevColor(r.severity_text)) },
    }], layout({ margin: { l: 90, r: 20, t: 10, b: 40 } }), PLOT_CFG);
  }
  const logs = await j("api/logs?limit=200");
  const t = document.getElementById("logsTable");
  if (logs.length === 0) { t.innerHTML = '<div class="empty">No logs received yet.</div>'; return; }
  t.innerHTML = `<table><thead><tr><th>Time</th><th>Severity</th><th>Service</th><th>Body</th></tr></thead><tbody>` +
    logs.map(r => `<tr><td>${new Date(r.ts).toLocaleTimeString()}</td>` +
      `<td class="sev ${sevClass(r.severity_text)}">${r.severity_text||""}</td>` +
      `<td>${r.service_name||""}</td><td class="body">${escapeHtml(r.body||"")}</td></tr>`).join("") +
    `</tbody></table>`;
}

async function loadTraces() {
  const slow = await j("api/spans/slowest?limit=15");
  const el = document.getElementById("slowChart");
  if (slow.length === 0) { el.innerHTML = '<div class="empty">No spans received yet.</div>'; }
  else {
    const rev = slow.slice().reverse();
    Plotly.react(el, [{
      x: rev.map(r => r.duration_ms), y: rev.map(r => r.name),
      type: "bar", orientation: "h",
      marker: { color: rev.map(r => r.status_code === "ERROR" ? cssVar("--critical") : cssVar("--series-3")) },
      customdata: rev.map(r => r.service_name),
      hovertemplate: "%{y}<br>%{x:.1f} ms<br>%{customdata}<extra></extra>",
    }], layout({ margin: { l: 220, r: 20, t: 10, b: 40 } }), PLOT_CFG);
  }
  const stats = await j("api/spans/stats?limit=15");
  const st = document.getElementById("spanStats");
  if (stats.length === 0) { st.innerHTML = '<div class="empty">No spans received yet.</div>'; }
  else {
    st.innerHTML = `<table><thead><tr><th>Span</th><th class="num">Calls</th><th class="num">Avg ms</th><th class="num">p95 ms</th><th class="num">Errors</th></tr></thead><tbody>` +
      stats.map(r => `<tr><td>${escapeHtml(r.name)}</td><td class="num">${fmtNum(r.calls)}</td>` +
        `<td class="num">${r.avg_ms!=null?r.avg_ms.toFixed(1):"—"}</td>` +
        `<td class="num">${r.p95_ms!=null?r.p95_ms.toFixed(1):"—"}</td>` +
        `<td class="num">${fmtNum(r.errors)}</td></tr>`).join("") + `</tbody></table>`;
  }
  const spans = await j("api/spans?limit=200");
  const t = document.getElementById("spansTable");
  if (spans.length === 0) { t.innerHTML = '<div class="empty">No spans received yet.</div>'; return; }
  t.innerHTML = `<table><thead><tr><th>Time</th><th>Span</th><th>Kind</th><th class="num">Duration</th><th>Status</th><th>Service</th></tr></thead><tbody>` +
    spans.map(r => `<tr><td>${new Date(r.ts).toLocaleTimeString()}</td><td>${escapeHtml(r.name)}</td>` +
      `<td>${r.kind}</td><td class="num">${r.duration_ms!=null?r.duration_ms.toFixed(1)+" ms":"—"}</td>` +
      `<td class="st-${r.status_code}">${r.status_code}</td><td>${r.service_name||""}</td></tr>`).join("") +
    `</tbody></table>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function selectTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === "panel-" + tab));
  refreshTab();
}

function refreshTab() {
  if (currentTab === "metrics") loadMetricNames();
  else if (currentTab === "logs") loadLogs();
  else if (currentTab === "traces") loadTraces();
}

function toggleTheme() {
  const root = document.documentElement;
  const dark = root.getAttribute("data-theme") === "dark";
  root.setAttribute("data-theme", dark ? "light" : "dark");
  document.getElementById("themeBtn").textContent = dark ? "🌙 Dark" : "☀ Light";
  refreshTab();
}

function tick() { refreshSummary(); refreshTab(); }
tick();
setInterval(tick, 15000);
</script>
</body>
</html>
"""
