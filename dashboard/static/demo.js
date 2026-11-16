// DEMO mode (app.py --demo): Specifications panel, S1 ramp chart and the simulator controls.
// Inactive unless the backend announces DEMO mode. Requests go to the local backend, which
// forwards them to the SIMULATOR (tools/fake_rig.py) only, never to an ESP32.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const RAMP_COLOR = { 10: "--s1", 20: "--s2", 30: "--s3" };
  const MARK = { PASS: "✓", FAIL: "✕", PENDING: "◷" };
  let active = false;
  let cards = [];
  let state = {};
  let s1 = null;            // S1 card view from the backend
  let hoverX = null;        // S1 chart hover position (px), null = none

  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  function send(obj) { if (active && typeof backendSend === "function") backendSend({ type: "demo", ...obj }); }
  function runScenario(id) { if (id) send({ action: "scenario", id }); }

  function enable(m) {
    if (active) return;
    active = true;
    for (const id of ["demo-tag", "demo-bar", "spec-panel"]) $(id).hidden = false;
    document.body.classList.add("is-demo");
    const sel = $("demo-scn");
    for (const sc of m.scenarios) {
      const o = document.createElement("option");
      o.value = sc.id;
      o.textContent = sc.title;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => runScenario(sel.value));
    for (const b of $("demo-speed").querySelectorAll("button")) {
      b.addEventListener("click", () => send({ action: "speed", x: Number(b.dataset.x) }));
    }
    $("demo-reset").addEventListener("click", () => send({ action: "reset" }));
    $("spec-grid").addEventListener("click", (ev) => {
      const c = ev.target.closest(".spec");
      if (c) runScenario(c.dataset.id);
    });
    $("spec-grid").addEventListener("keydown", (ev) => {
      const c = ev.target.closest(".spec");
      if (c && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); runScenario(c.dataset.id); }
    });
    const cv = $("s1-chart");
    cv.addEventListener("mousemove", (ev) => { hoverX = ev.offsetX; drawS1(); });
    cv.addEventListener("mouseleave", () => { hoverX = null; drawS1(); });
    new ResizeObserver(drawS1).observe(cv);
  }

  // Resources this page loaded (C4): all must come from this PC.
  function pageResources() {
    const list = performance.getEntriesByType("resource");
    const ext = list.filter((r) => { try { return new URL(r.name).host !== location.host; } catch (e) { return true; } });
    return { n: list.length + 1, ext: ext.length };
  }

  function renderCards() {
    const grid = $("spec-grid");
    const html = cards.map((c) => {
      let detail = c.detail.slice();
      let verdict = c.verdict;
      if (c.id === "C4" && c.ran) {
        const r = pageResources();
        detail.push(`This page loaded ${r.n} resources, ${r.ext} from outside this PC`);
        if (r.ext > 0) verdict = "FAIL";
      }
      return `<div class="spec v-${verdict}${c.running ? " running" : ""}" data-id="${c.id}" role="button" tabindex="0"
          aria-label="${esc(c.id)}: ${esc(verdict)}. Run the ${esc(c.id)} scenario">
        <div class="spec-top"><b class="spec-id">${esc(c.id)}</b><span class="owner">${esc(c.owner)}</span>
          ${c.running ? '<span class="run-dot">● running</span>' : ""}
          <span class="verdict">${MARK[verdict] || ""} ${esc(verdict)}</span></div>
        <div class="spec-title">${esc(c.title)}</div>
        <p class="spec-text">“${esc(c.spec)}”</p>
        <div class="spec-row"><span class="label">Acceptance</span><span>${esc(c.criterion)}</span></div>
        <div class="spec-row"><span class="label">Measured</span><b class="mono">${esc(c.measured)}</b></div>
        ${detail.length ? `<ul class="spec-detail">${detail.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}
      </div>`;
    }).join("");
    if (grid.dataset.html !== html) { grid.innerHTML = html; grid.dataset.html = html; }
  }

  function renderBar() {
    const sid = state.scenario || "";
    const sel = $("demo-scn");
    if (document.activeElement !== sel) sel.value = sid;
    $("demo-phase").textContent = state.phase ? state.phase + (state.wifi_down ? " · Wi-Fi down" : "") : "idle";
    for (const b of $("demo-speed").querySelectorAll("button")) {
      b.setAttribute("aria-pressed", String(Number(b.dataset.x) === state.speed));
    }
    $("c4-banner").hidden = sid !== "C4";
    $("s1-panel").hidden = !(s1 && (s1.ran || sid === "S1"));
  }

  function renderS1Table() {
    if (!s1) return;
    const rows = (s1.ramps || []).map((r) => `<tr class="v-${r.verdict}">
        <td><i class="dot" style="background:var(${RAMP_COLOR[r.rate]})"></i>${r.rate} mbar/min</td>
        <td class="mono">${r.rate.toFixed(1)}</td>
        <td class="mono">${r.band[0]}–${r.band[1]}</td>
        <td class="mono"><b>${r.slope == null ? "--" : r.slope.toFixed(2)}</b>${r.active ? ' <span class="muted">(live)</span>' : ""}</td>
        <td class="mono">${r.n}</td>
        <td class="verdict">${MARK[r.verdict]} ${r.verdict}</td></tr>`).join("");
    $("s1-rows").innerHTML = rows;
    $("s1-summary").textContent = s1.summary;
    $("s1-foot").textContent = s1.summary;
    const ts = s1.time_scale || 1;
    $("s1-axis-note").textContent = `time axis: process time (s) from each ramp start · demo speed ${state.speed || 1}×` +
      (ts !== 1 ? " (plant runs faster; slopes are per process minute)" : "");
  }

  // S1 chart: pressure vs process time per ramp, dashed target slope, shaded ±10 % band.
  function drawS1() {
    const cv = $("s1-chart");
    if (!s1 || $("s1-panel").hidden) return;
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const L = 44, R = 96, T = 10, B = 30;
    const pw = W - L - R, ph = H - T - B;
    const ramps = s1.ramps || [];
    let tmax = 40;
    for (const r of ramps) for (const p of r.pts) tmax = Math.max(tmax, p[0]);
    tmax = Math.ceil(tmax / 10) * 10;
    const ymax = 16;
    const X = (t) => L + (t / tmax) * pw;
    const Y = (v) => T + (1 - v / ymax) * ph;
    const ink = css("--muted"), grid = css("--grid");
    g.font = "11px " + css("--mono");
    g.lineWidth = 1; g.strokeStyle = grid; g.fillStyle = ink;
    g.textAlign = "right"; g.textBaseline = "middle";
    for (let v = 0; v <= ymax; v += 5) { g.beginPath(); g.moveTo(L, Y(v) + .5); g.lineTo(L + pw, Y(v) + .5); g.stroke(); g.fillText(String(v), L - 6, Y(v)); }
    g.textAlign = "center"; g.textBaseline = "top";
    const xs = tmax > 60 ? 20 : 10;
    for (let t = 0; t <= tmax; t += xs) { g.beginPath(); g.moveTo(X(t) + .5, T); g.lineTo(X(t) + .5, T + ph); g.stroke(); g.fillText(`${t} s`, X(t), T + ph + 8); }
    // 15 mbar(g) stop line
    g.setLineDash([2, 4]); g.strokeStyle = ink;
    g.beginPath(); g.moveTo(L, Y(15)); g.lineTo(L + pw, Y(15)); g.stroke();
    g.setLineDash([]);
    g.textAlign = "left"; g.textBaseline = "middle"; g.fillText("15 stop", L + pw + 6, Y(15));

    g.save(); g.beginPath(); g.rect(L, T, pw, ph); g.clip();
    for (const r of ramps) {
      const col = css(RAMP_COLOR[r.rate]);
      const p0 = r.p0 || 0;
      const lo = r.band[0] / 60, hi = r.band[1] / 60, k = r.rate / 60;
      // band: wedge between the -10 % and +10 % slopes
      g.globalAlpha = 0.13; g.fillStyle = col;
      g.beginPath(); g.moveTo(X(0), Y(p0)); g.lineTo(X(tmax), Y(p0 + hi * tmax)); g.lineTo(X(tmax), Y(p0 + lo * tmax)); g.closePath(); g.fill();
      g.globalAlpha = 1;
      g.setLineDash([6, 4]); g.lineWidth = 1.5; g.strokeStyle = col;
      g.beginPath(); g.moveTo(X(0), Y(p0)); g.lineTo(X(tmax), Y(p0 + k * tmax)); g.stroke();
      g.setLineDash([]);
      if (r.pts.length) {
        g.lineWidth = 2; g.lineJoin = "round"; g.strokeStyle = col;
        g.beginPath();
        r.pts.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1]))));
        g.stroke();
      }
    }
    g.restore();
    // direct labels at the end of each measured trace (text in ink, the dot carries identity)
    g.textAlign = "left"; g.textBaseline = "middle"; g.font = "600 11px " + css("--sans");
    let lastY = -99;
    for (const r of ramps.slice().sort((a, b) => b.rate - a.rate)) {
      if (!r.pts.length) continue;
      const last = r.pts[r.pts.length - 1];
      let y = Math.max(T + 6, Y(last[1]));
      if (Math.abs(y - lastY) < 14) y = lastY + 14;
      lastY = y;
      const x = Math.min(X(last[0]) + 8, L + pw + 6);
      g.fillStyle = css(RAMP_COLOR[r.rate]); g.beginPath(); g.arc(x + 3, y, 4, 0, 7); g.fill();
      g.fillStyle = css("--ink");
      g.fillText(`${r.rate}${r.slope != null ? " → " + r.slope.toFixed(1) : ""}`, x + 10, y);
    }
    g.strokeStyle = css("--line"); g.lineWidth = 1; g.strokeRect(L + .5, T + .5, pw, ph);
    g.fillStyle = ink; g.textAlign = "left"; g.textBaseline = "top"; g.font = "11px " + css("--mono");
    g.fillText("mbar(g)", 4, T);

    // hover: crosshair + tooltip
    const tip = $("s1-tip");
    if (hoverX == null || hoverX < L || hoverX > L + pw) { tip.hidden = true; return; }
    const t = ((hoverX - L) / pw) * tmax;
    g.strokeStyle = ink; g.lineWidth = 1;
    g.beginPath(); g.moveTo(hoverX + .5, T); g.lineTo(hoverX + .5, T + ph); g.stroke();
    const lines = [`<b>t = ${t.toFixed(1)} s</b>`];
    for (const r of ramps) {
      let best = null;
      for (const p of r.pts) if (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      const tgt = (r.p0 || 0) + r.rate / 60 * t;
      const meas = best && Math.abs(best[0] - t) < tmax / 40 ? best[1].toFixed(2) : "--";
      lines.push(`<span><i class="dot" style="background:var(${RAMP_COLOR[r.rate]})"></i>${r.rate}: ${meas} · target ${tgt.toFixed(2)}</span>`);
    }
    tip.innerHTML = lines.join("<br>");
    tip.hidden = false;
    tip.style.left = `${Math.min(hoverX + 12, W - 190)}px`;
    tip.style.top = `${T + 8}px`;
  }

  window.demo = {
    on(m) {
      if (m.type === "mode" && m.demo) { enable(m); return; }
      if (!active) return;
      if (m.type === "specs") {
        cards = m.cards;
        state = m.demo || {};
        s1 = cards.find((c) => c.id === "S1") || null;
        renderCards();
        renderBar();
        renderS1Table();
        drawS1();
      } else if (m.type === "reset") {
        cards = []; state = {}; s1 = null;
        renderCards(); renderBar();
      }
    },
  };
})();
