// Minimal local time-series chart (canvas). No external library: the laptop has no
// Internet while it is joined to the ESP32 access point.
"use strict";

class PressureChart {
  constructor(canvas, opts) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.windowMs = opts.windowMs;
    this.lines = opts.lines; // [{v, label, color}]
    this.data = [];          // sorted by seq: {seq, t, pf, raw, valid}
    this.dirty = true;
    new ResizeObserver(() => { this.dirty = true; }).observe(canvas);
    const tick = () => { if (this.dirty) this.draw(); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }

  css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  // Insert keeping seq order (replayed samples can arrive late).
  add(s) {
    const p = { seq: s.seq, t: s.t_esp_ms, pf: s.P_f, raw: s.P_raw, valid: s.valid };
    const d = this.data;
    if (!d.length || d[d.length - 1].seq < p.seq) d.push(p);
    else {
      let lo = 0, hi = d.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (d[m].seq < p.seq) lo = m + 1; else hi = m; }
      if (d[lo] && d[lo].seq === p.seq) return;
      d.splice(lo, 0, p);
    }
    const newest = d[d.length - 1].t;
    let cut = 0;
    while (cut < d.length && d[cut].t < newest - this.windowMs) cut++;
    if (cut) d.splice(0, cut);
    this.dirty = true;
  }

  reset() { this.data = []; this.dirty = true; }

  draw() {
    this.dirty = false;
    const dpr = window.devicePixelRatio || 1;
    const W = this.c.clientWidth, H = this.c.clientHeight;
    if (this.c.width !== Math.round(W * dpr) || this.c.height !== Math.round(H * dpr)) {
      this.c.width = Math.round(W * dpr);
      this.c.height = Math.round(H * dpr);
    }
    const g = this.ctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    const L = 44, R = 64, T = 10, B = 26;
    const pw = W - L - R, ph = H - T - B;
    const d = this.data;
    const newest = d.length ? d[d.length - 1].t : 0;
    const x0 = newest - this.windowMs;

    let ymin = 0, ymax = 32;
    for (const p of d) {
      for (const v of [p.pf, p.raw]) {
        if (v == null) continue;
        if (v < ymin) ymin = Math.floor(v) - 1;
        if (v > ymax) ymax = Math.ceil(v) + 1;
      }
    }
    const X = (t) => L + ((t - x0) / this.windowMs) * pw;
    const Y = (v) => T + (1 - (v - ymin) / (ymax - ymin)) * ph;

    const ink = this.css("--muted"), grid = this.css("--grid"), font = "11px " + this.css("--mono");
    g.font = font;
    g.lineWidth = 1;

    // grid + y labels
    g.strokeStyle = grid; g.fillStyle = ink; g.textAlign = "right"; g.textBaseline = "middle";
    const ystep = (ymax - ymin) > 40 ? 10 : 5;
    for (let v = Math.ceil(ymin / ystep) * ystep; v <= ymax; v += ystep) {
      g.beginPath(); g.moveTo(L, Y(v) + .5); g.lineTo(L + pw, Y(v) + .5); g.stroke();
      g.fillText(String(v), L - 6, Y(v));
    }
    // x labels: seconds before newest sample
    g.textAlign = "center"; g.textBaseline = "top";
    for (let s = 0; s <= this.windowMs / 1000; s += 60) {
      const x = L + pw - (s * 1000 / this.windowMs) * pw;
      g.beginPath(); g.moveTo(x + .5, T); g.lineTo(x + .5, T + ph); g.stroke();
      g.fillText(s === 0 ? "now" : `-${s / 60} min`, x, T + ph + 6);
    }

    // threshold lines
    g.setLineDash([6, 4]); g.lineWidth = 1.5; g.textAlign = "left"; g.textBaseline = "middle";
    for (const ln of this.lines) {
      if (ln.v < ymin || ln.v > ymax) continue;
      const col = this.css(ln.color);
      g.strokeStyle = col; g.fillStyle = col;
      g.beginPath(); g.moveTo(L, Y(ln.v)); g.lineTo(L + pw, Y(ln.v)); g.stroke();
      g.fillText(`${ln.v} ${ln.label}`, L + pw + 6, Y(ln.v));
    }
    g.setLineDash([]);

    // series; break the line on gaps (missing seq) and on invalid samples
    const series = (key, color, width) => {
      g.strokeStyle = this.css(color); g.lineWidth = width; g.lineJoin = "round";
      g.beginPath();
      let pen = false, prevSeq = -2;
      for (const p of d) {
        const v = p[key];
        const ok = v != null && (key === "pf" || p.valid);
        if (!ok || p.seq !== prevSeq + 1) pen = false;
        prevSeq = p.seq;
        if (!ok) continue;
        const x = X(p.t), y = Y(v);
        if (pen) g.lineTo(x, y); else g.moveTo(x, y);
        pen = true;
      }
      g.stroke();
    };
    g.save();
    g.beginPath(); g.rect(L, T, pw, ph); g.clip();
    series("raw", "--raw", 1);
    series("pf", "--pf", 2.2);
    g.restore();

    g.strokeStyle = this.css("--line"); g.lineWidth = 1;
    g.strokeRect(L + .5, T + .5, pw, ph);
    g.fillStyle = ink; g.textAlign = "left"; g.textBaseline = "top";
    g.fillText("mbar(g)", 4, T);
    if (!d.length) {
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText("no samples yet", L + pw / 2, T + ph / 2);
    }
  }
}
