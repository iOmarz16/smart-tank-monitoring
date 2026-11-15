// M021 dashboard front end: view only. It never sends anything to the rig; the local
// backend (app.py) forwards samples and statistics over ws://127.0.0.1:<port>/ws.
"use strict";

const $ = (id) => document.getElementById(id);
const STALE_MS = 1500;   // link indicator turns amber after this
const LOST_MS = 3000;    // "Wi-Fi lost" after this

// ---- theme (light / dark), remembered per browser ----------------------------
(function theme() {
  let t = null;
  try { t = localStorage.getItem("m021-theme"); } catch (e) { /* storage blocked */ }
  if (!t) t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
  $("theme").addEventListener("click", () => {
    const n = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = n;
    try { localStorage.setItem("m021-theme", n); } catch (e) { /* ignore */ }
    chart.dirty = true;
  });
})();

const chart = new PressureChart($("chart"), {
  windowMs: 5 * 60 * 1000,
  lines: [
    { v: 10, label: "SP", color: "--accent" },
    { v: 14, label: "warn", color: "--warn" },
    { v: 20, label: "limit", color: "--trip" },
    { v: 29, label: "hard", color: "--hard" },
  ],
});

let latest = null;       // newest sample by seq
let lastStatus = null;
let lastStatusAt = 0;

const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "--" : Number(v).toFixed(d));
const fmtMs = (v) => (v == null ? "--" : `${Number(v).toFixed(1)} ms`);

function render() {
  const s = latest;
  const st = lastStatus;
  // link age: backend value + time since that status arrived
  const age = st && st.age_ms != null ? st.age_ms + (performance.now() - lastStatusAt) : null;
  const connected = st && st.connected;
  const lost = !connected || age == null || age > LOST_MS;

  // link pill
  const link = $("link");
  if (!connected) { link.className = "link link-down"; $("link-text").textContent = "Wi-Fi lost · انقطع الاتصال"; }
  else if (age == null || age > STALE_MS) { link.className = "link link-stale"; $("link-text").textContent = `No data ${age == null ? "" : (age / 1000).toFixed(1) + " s"}`; }
  else { link.className = "link link-up"; $("link-text").textContent = `Live · ${Math.round(age)} ms`; }

  // status block
  const box = $("status");
  if (!s || lost) {
    box.className = "status s-nodata";
    $("status-main").textContent = "NO DATA";
    $("status-ar").textContent = "لا توجد بيانات";
    $("status-sub").textContent = s ? `last: ${s.state}${s.trip ? " / TRIP " + s.trip_reason : ""}` : "waiting for the rig";
  } else if (s.trip) {
    box.className = "status s-trip";
    $("status-main").textContent = "TRIP";
    $("status-ar").textContent = "فصل أمان";
    $("status-sub").textContent = `${s.trip_reason} · reset only at the rig (serial RESET)`;
  } else if (s.warning) {
    box.className = "status s-warning";
    $("status-main").textContent = "WARNING";
    $("status-ar").textContent = "تحذير";
    $("status-sub").textContent = s.P_f >= 14 ? "P_f ≥ 14 mbar(g)" : `predicted 20 mbar(g) in ${fmt(s.t_to_limit, 1)} s`;
  } else {
    box.className = "status s-normal";
    $("status-main").textContent = "NORMAL";
    $("status-ar").textContent = "طبيعي";
    $("status-sub").textContent = s.valid ? "sensor valid" : "sensor fault (holding)";
  }

  if (s) {
    $("pf").textContent = fmt(s.P_f);
    $("praw").textContent = fmt(s.P_raw);
    $("slope").textContent = fmt(s.slope, 3);
    $("uopen").textContent = fmt(s.u_open, 1);
    $("uopen-bar").style.width = `${Math.max(0, Math.min(100, s.u_open || 0))}%`;
    $("pump").textContent = s.pump_on ? "ON" : "OFF";
    $("pump").className = "val " + (s.pump_on ? "on" : "off");
    const v = s.valve_duty;
    $("valve").textContent = v <= 0 ? "OPEN" : v >= 100 ? "CLOSED" : "PWM";
    $("valve-sub").textContent = `commanded · energized ${fmt(v, 1)} %`;
    $("ttl").textContent = s.t_to_limit == null ? "--" : fmt(s.t_to_limit, 1);
    $("state").textContent = s.state;
    $("state").className = "val st-" + s.state;
    $("state-sub").textContent = `seq ${s.seq} · loop ${fmt(s.loop_period_us / 1000, 1)} ms`;
  }

  if (st) {
    $("age").textContent = age == null ? "--" : `${(age / 1000).toFixed(2)} s`;
    $("lat-med").textContent = fmtMs(st.lat_median);
    $("lat-p95").textContent = fmtMs(st.lat_p95);
    $("lat-max").textContent = fmtMs(st.lat_max);
    $("lat-max").className = st.lat_max != null && st.lat_max > 1000 ? "bad" : "";
    $("lat-n").textContent = st.lat_n;
    $("lat-over").textContent = st.lat_over_1s;
    $("lat-over").className = st.lat_over_1s > 0 ? "bad" : "good";
    $("offset").textContent = st.offset_ms == null ? "estimating…" : `${fmt(st.offset_ms, 1)} ms`;
    $("rtt").textContent = fmtMs(st.offset_rtt_ms);
    $("nsamp").textContent = st.samples;
    $("missing").textContent = st.missing;
    $("missing").className = st.missing > 0 ? "bad" : "good";
    $("replayed").textContent = st.replayed;
    $("csv").textContent = st.csv;
  }
}

// ---- local WebSocket to the backend ------------------------------------------------
function connectBackend() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "history") {
      chart.reset();
      for (const s of m.samples) { chart.add(s); if (!latest || s.seq > latest.seq) latest = s; }
    } else if (m.type === "sample") {
      const s = m.s;
      chart.add(s);
      if (!latest || s.seq > latest.seq) latest = s;
    } else if (m.type === "status") {
      lastStatus = m;
      lastStatusAt = performance.now();
    }
  };
  ws.onclose = () => {
    lastStatus = lastStatus ? { ...lastStatus, connected: false } : null;
    setTimeout(connectBackend, 1000);
  };
}
connectBackend();
setInterval(render, 100);
