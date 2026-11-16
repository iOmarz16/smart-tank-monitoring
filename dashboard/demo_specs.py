"""DEMO mode: live verification board, one card per specification (Table 1).

Fed with the same samples the dashboard logs (plus the demo_* fields that only the
fake rig adds in DEMO mode). Each card is measured from its own scenario run only, and
keeps its last result while other scenarios run. Verdicts:
    PENDING  not enough data yet (or the scenario has not been run)
    PASS     the acceptance criterion is met by the measured values
    FAIL     the acceptance criterion is not met
Nothing here sends anything to the rig: it only reads samples.
"""

from __future__ import annotations

import math
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

S1_BANDS = {10: (9.0, 11.0), 20: (18.0, 22.0), 30: (27.0, 33.0)}  # +/-10 %
S1_FIT_MAX_P = 15.0
S1_CHART_POINTS = 150          # per ramp, sent to the browser
S4_MIN_UPDATES = 300           # 30 s of live updates (I1 on the rig: 10 min, >= 6000)
S5_MAX_PERIOD_MS = 110.0       # I2 acceptance
S5_MEAN_TOL_MS = 1.0           # I2: mean period 100 ms
IS2_BAND = 2.0
IS2_CONFIRM_S = 3.0            # back inside 10 +/- 2 for 3 s counts as "returned"
IS2_LIMIT_S = 30.0
C4_MIN_RUN_S = 10.0

# Specification texts: Table 1 / Section 2.1.7 of the Third Report (S1 as worded by the team).
CARDS = [
    dict(id="S1", owner="CHE", title="Specification 1 · ramp rate",
         spec="5.0 ± 0.2 L gas headspace reproducing 10/20/30 mbar/min pressure ramps within ±10%",
         criterion="Fitted slope over 0–15 mbar(g) within 9–11 / 18–22 / 27–33 mbar/min (T1)"),
    dict(id="S4", owner="ICS", title="Specification 4 · UI latency",
         spec="User interface updates system parameters with response time ≤ 1 s.",
         criterion=f"Every live update ≤ 1.0 s (median / p95 / max; {S4_MIN_UPDATES} updates in the demo, I1)"),
    dict(id="S5", owner="ICS", title="Specification 5 · loop and logging",
         spec="Sensor acquisition/control loop ≥ 10 Hz; every trial logged ≥ 5 Hz on a single clock.",
         criterion="Mean period 100 ms, none > 110 ms; ≥ 5 samples in every 1 s; missing seq = 0 after a 20 s Wi-Fi outage (I2)"),
    dict(id="S6a", owner="ICS", title="Specification 6 · 20 mbar(g) trip",
         spec="On 20 mbar(g) limit or sensor loss > 0.5 s: injection stops, vent opens ≤ 1.0 s, w/o dashboard or network.",
         criterion="Pump OFF and vent open; trip decision → outputs ≤ 1.0 s (I3a)"),
    dict(id="S6b", owner="ICS", title="Specification 6 · sensor loss",
         spec="On 20 mbar(g) limit or sensor loss > 0.5 s: injection stops, vent opens ≤ 1.0 s, w/o dashboard or network.",
         criterion="SENSOR_LOSS 0.5–0.6 s after the last valid reading (one 100 ms loop); pump OFF, vent open ≤ 1.0 s (I3b)"),
    dict(id="IS1", owner="CHE+ICS", title="Integrated Spec. 1 · early warning",
         spec="Predictive warning ≥ 10 s before reaching the 20 mbar(g) safety limit.",
         criterion="Warning lead before PT-101 reads 20 mbar(g) ≥ 10 s at 10, 20 and 30 mbar/min (T7, I6a)"),
    dict(id="IS2", owner="CHE+ICS", title="Integrated Spec. 2 · disturbance recovery",
         spec="Pressure returns to 10 ± 2 mbar(g) within 30 s after a disturbance.",
         criterion="Back inside 8–12 mbar(g) (and staying 3 s) ≤ 30 s after the disturbance (T6, P6)"),
    dict(id="IS3", owner="CHE+ICS", title="Integrated Spec. 3 · safety response",
         spec="System safety response activates ≤ 1.0 s when pressure reaches 20 mbar(g).",
         criterion="First PT-101 sample ≥ 20 mbar(g) (t1) → pump OFF and vent open (t3) ≤ 1.0 s (I3a)"),
    dict(id="C4", owner="ICS", title="Constraint 4 · stand-alone operation",
         spec="All core ICS functions shall operate locally without relying on an Internet connection or cloud service.",
         criterion="No external address in the firmware or dashboard code; control runs on the controller (I4)"),
]
CARD_IDS = [c["id"] for c in CARDS]


def fit_slope(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares line through (t, p). Returns (slope per s, intercept) or None."""
    n = len(pts)
    if n < 3:
        return None
    mt = sum(t for t, _ in pts) / n
    mp = sum(p for _, p in pts) / n
    stt = sum((t - mt) ** 2 for t, _ in pts)
    if stt <= 1e-12:
        return None
    b = sum((t - mt) * (p - mp) for t, p in pts) / stt
    return b, mp - b * mt


def scan_external_hosts() -> list[str]:
    """I4 / C4: external addresses in the firmware and dashboard code (should be none)."""
    allowed = {"127.0.0.1", "localhost", "192.168.4.1"}
    files = [*sorted((ROOT / "firmware" / "src").glob("*.*")),
             *sorted((ROOT / "firmware" / "include").glob("*.*")),
             *sorted((ROOT / "dashboard").glob("*.py")),
             *sorted((ROOT / "dashboard" / "static").glob("*.*")),
             ROOT / "tools" / "fake_rig.py", ROOT / "tools" / "demo_director.py"]
    url = re.compile(r"\b(?:https?|wss?|ftp|mqtt)://([^/\s\"'`<>:]+)")
    bare = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|cloud|dev|app|gov|edu)\b", re.I)
    hits = []
    for f in files:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in url.finditer(text):
            host = m.group(1)
            if host in allowed or host.startswith(("$", "{")):
                continue
            hits.append(f"{f.relative_to(ROOT).as_posix()}: {host}")
        for m in bare.finditer(text):
            hits.append(f"{f.relative_to(ROOT).as_posix()}: {m.group(0)}")
    return hits


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


class Card:
    def __init__(self, meta: dict) -> None:
        self.meta = meta
        self.reset()

    def reset(self) -> None:
        self.ran = False
        self.verdict = "PENDING"
        self.measured = "not run yet"
        self.detail: list[str] = []
        self.extra: dict = {}

    def view(self, running: bool) -> dict:
        return {**self.meta, "verdict": self.verdict, "measured": self.measured, "detail": self.detail,
                "running": running, "ran": self.ran, **self.extra}

    def start(self) -> None:
        self.reset()
        self.ran = True
        self.measured = "running…"

    def sample(self, s: dict) -> None:  # pragma: no cover - overridden
        pass

    def tick(self, demo_state: dict, running: bool) -> None:
        pass


class S1Card(Card):
    def reset(self) -> None:
        super().reset()
        self.ramps = {r: {"rate": r, "band": list(S1_BANDS[r]), "pts": [], "t0": None, "p0": None,
                          "done": False, "slope": None, "verdict": "PENDING"} for r in S1_BANDS}
        self.extra = {"headspace_l": 5.0, "ramps": [], "summary": "S1: 0/3 ramps within ±10 %", "time_scale": 1}

    def sample(self, s: dict) -> None:
        phase, mark = s.get("demo_phase") or "", s.get("demo_mark") or ""
        m = re.match(r"S1 ramp (\d+) (start|stop)$", mark)
        if m and int(m.group(1)) in self.ramps:
            r = self.ramps[int(m.group(1))]
            if m.group(2) == "start":
                r["t0"] = s.get("demo_t_s")
                r["p0"] = _num(s.get("P_f"))
            else:
                r["done"] = True
                self._fit(r)
        m = re.match(r"S1 ramp (\d+)$", phase)
        if m and int(m.group(1)) in self.ramps:
            r = self.ramps[int(m.group(1))]
            p, t = _num(s.get("P_raw")), s.get("demo_t_s")
            if (r["t0"] is not None and s.get("pump_on") and s.get("valid") and p is not None
                    and t is not None and 0.0 <= p <= S1_FIT_MAX_P):
                r["pts"].append((round(t - r["t0"], 3), round(p, 3)))
                if len(r["pts"]) % 10 == 0:
                    self._fit(r)
        if s.get("demo_speed"):
            self.extra["time_scale"] = s["demo_speed"]

    def _fit(self, r: dict) -> None:
        f = fit_slope(r["pts"])
        r["slope"] = None if f is None else f[0] * 60.0
        lo, hi = r["band"]
        if r["done"]:
            r["verdict"] = "PASS" if r["slope"] is not None and lo <= r["slope"] <= hi else "FAIL"

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        out, slopes = [], []
        for rate, r in self.ramps.items():
            pts = r["pts"]
            stride = max(1, math.ceil(len(pts) / S1_CHART_POINTS))
            dec = pts[::stride]
            if pts and dec[-1] != pts[-1]:
                dec.append(pts[-1])
            out.append({"rate": rate, "band": r["band"], "slope": r["slope"], "done": r["done"],
                        "verdict": r["verdict"], "n": len(pts), "p0": r["p0"] or 0.0,
                        "active": r["t0"] is not None and not r["done"], "pts": dec})
            slopes.append("--" if r["slope"] is None else f"{r['slope']:.2f}")
        n_pass = sum(1 for r in self.ramps.values() if r["verdict"] == "PASS")
        n_done = sum(1 for r in self.ramps.values() if r["done"])
        self.extra["ramps"] = out
        self.extra["summary"] = f"S1: {n_pass}/3 ramps within ±10 %"
        self.measured = " / ".join(slopes) + " mbar/min"
        if any(r["verdict"] == "FAIL" for r in self.ramps.values()):
            self.verdict = "FAIL"
        elif n_done == 3:
            self.verdict = "PASS"
        else:
            self.verdict = "PENDING"
        self.detail = ["Headspace 5.0 L (set by CHE); vent closed, pump ON, TEST mode",
                       "Slope fitted to PT-101 samples between 0 and 15 mbar(g) against process time"]


class S4Card(Card):
    def reset(self) -> None:
        super().reset()
        self.lat: list[float] = []

    def sample(self, s: dict) -> None:
        if not s.get("replayed") and s.get("latency_ms") is not None:
            self.lat.append(float(s["latency_ms"]))

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        n = len(self.lat)
        if not n:
            self.measured = "waiting for live updates (clock offset)…"
            return
        lat = sorted(self.lat)
        med = statistics.median(lat)
        p95 = lat[min(n - 1, int(round(0.95 * (n - 1))))]
        mx = lat[-1]
        over = sum(1 for v in lat if v > 1000.0)
        self.measured = f"median {med:.1f} ms · p95 {p95:.1f} ms · max {mx:.1f} ms"
        self.detail = [f"{n} live updates measured ({min(n, S4_MIN_UPDATES)}/{S4_MIN_UPDATES})",
                       f"{over} over 1.0 s", "Latency = arrival on this PC − ESP32 timestamp + clock offset"]
        self.extra = {"lat_n": n, "lat_median": med, "lat_p95": p95, "lat_max": mx}
        if over:
            self.verdict = "FAIL"
        elif n >= S4_MIN_UPDATES:
            self.verdict = "PASS"
        else:
            self.verdict = "PENDING"


class S5Card(Card):
    def reset(self) -> None:
        super().reset()
        self.seqs: dict[int, tuple[float, float]] = {}   # seq -> (t_esp_ms, loop_period_us)
        self.down_seen = self.up_seen = False
        self.replayed = 0

    def sample(self, s: dict) -> None:
        seq = s.get("seq")
        if not isinstance(seq, int):
            return
        self.seqs[seq] = (float(s.get("t_esp_ms") or 0), float(s.get("loop_period_us") or 0))
        if s.get("replayed"):
            self.replayed += 1
        mark = s.get("demo_mark") or ""
        if mark == "wifi down":
            self.down_seen = True
        if mark == "wifi up":
            self.up_seen = True

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran or not self.seqs:
            return
        if running and demo_state.get("wifi_down"):
            self.down_seen = True
        lo, hi = min(self.seqs), max(self.seqs)
        missing = hi - lo + 1 - len(self.seqs)
        per = [p / 1000.0 for q, (_, p) in self.seqs.items() if q != lo and p > 0]
        mean = statistics.fmean(per) if per else float("nan")
        mx = max(per) if per else float("nan")
        rate = 1000.0 / mean if per else float("nan")
        win_txt = "—"
        min_win = None
        if missing == 0 and len(self.seqs) > 30:
            counts: dict[int, int] = {}
            for t, _ in self.seqs.values():
                counts[int(t // 1000)] = counts.get(int(t // 1000), 0) + 1
            full = sorted(counts)[1:-1]   # drop the partial first and last second
            if full:
                min_win = min(counts[k] for k in full)
                win_txt = f"{min_win}/s"
        wifi = ("Wi-Fi DOWN (simulated)" if running and demo_state.get("wifi_down")
                else "outage over, replaying" if self.up_seen and missing
                else "outage over, replay done" if self.up_seen
                else "outage at 10 s" if not self.down_seen else "Wi-Fi down")
        self.measured = (f"loop {rate:.2f} Hz · logged ≥ {win_txt} · missing seq {missing}")
        self.detail = [f"Loop period mean {mean:.2f} ms, max {mx:.1f} ms ({len(per)} periods)",
                       f"Lowest 1-s window: {win_txt}", f"20 s outage: {wifi}; {self.replayed} replayed samples",
                       "Every sample carries the ESP32 timestamp (single clock)"]
        self.extra = {"loop_hz": rate, "mean_ms": mean, "max_ms": mx, "missing": missing, "min_window": min_win}
        if per and mx > S5_MAX_PERIOD_MS:
            self.verdict = "FAIL"
        elif self.up_seen and missing == 0 and min_win is not None:
            ok = abs(mean - 100.0) <= S5_MEAN_TOL_MS and min_win >= 5
            self.verdict = "PASS" if ok else "FAIL"
        else:
            self.verdict = "PENDING"


class TripCard(Card):
    """S6a and IS3: the 20 mbar(g) trip from a blocked-vent ramp."""

    def __init__(self, meta: dict, reason: str = "SOFT_LIMIT") -> None:
        self.reason = reason
        super().__init__(meta)

    def reset(self) -> None:
        super().reset()
        self.t1_ms = None
        self.trip = None

    def sample(self, s: dict) -> None:
        p = _num(s.get("P_raw"))
        if self.t1_ms is None and p is not None and s.get("valid") and p >= 20.0:
            self.t1_ms = float(s["t_esp_ms"])
        if self.trip is None and s.get("trip") and s.get("trip_decision_us"):
            self.trip = dict(s)

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        if self.trip is None:
            self.measured = "ramping to 20 mbar(g)…"
            return
        s = self.trip
        dec, out = s["trip_decision_us"], s["trip_output_us"]
        t3 = s.get("t3_us") or out
        dec_out = (out - dec) / 1000.0 if out else None
        safe = (not s.get("pump_on")) and float(s.get("valve_duty") or 0) <= 0.0
        resp = (t3 - self.t1_ms * 1000.0) / 1000.0 if (self.t1_ms is not None and t3) else None
        self.extra = {"dec_out_ms": dec_out, "response_ms": resp, "reason": s.get("trip_reason"),
                      "peak_p": _num(s.get("P_raw"))}
        lines = [f"Trip {s.get('trip_reason')} at P_raw {s.get('P_raw')} mbar(g); pump "
                 f"{'OFF' if not s.get('pump_on') else 'ON'}, vent {'OPEN' if safe else 'not open'}",
                 f"t3 source: {s.get('t3_source')} (rig: MOSFET read-back or 240 fps film)"]
        if self.meta["id"] == "IS3":
            self.measured = "--" if resp is None else f"response {resp:.1f} ms (t1 → t3)"
            lines.insert(1, f"Decision → outputs {dec_out:.3f} ms" if dec_out is not None else "")
            lines.append("True-crossing check (U-tube film, I6b) is a rig test")
            ok = resp is not None and resp <= 1000.0 and safe
        else:
            self.measured = "--" if dec_out is None else f"decision → outputs {dec_out:.3f} ms"
            if resp is not None:
                lines.insert(1, f"First sample ≥ 20 (t1) → outputs (t3): {resp:.1f} ms")
            ok = dec_out is not None and dec_out <= 1000.0 and safe
        self.detail = [x for x in lines if x]
        self.verdict = "PASS" if ok and s.get("trip_reason") == self.reason else "FAIL"


class S6bCard(Card):
    def reset(self) -> None:
        super().reset()
        self.last_valid_ms = None
        self.lost = False
        self.trip = None

    def sample(self, s: dict) -> None:
        if (s.get("demo_mark") or "") == "signal lost":
            self.lost = True
        if self.trip is None:
            if s.get("valid"):
                self.last_valid_ms = float(s["t_esp_ms"])
            if s.get("trip") and s.get("trip_decision_us"):
                self.trip = dict(s)

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        if self.trip is None:
            self.measured = "signal lost, waiting for the trip…" if self.lost else "holding at 10 mbar(g)…"
            return
        s = self.trip
        base = self.last_valid_ms * 1000.0
        detect = (s["trip_decision_us"] - base) / 1e6
        out = (s["trip_output_us"] - base) / 1e6 if s["trip_output_us"] else None
        period_s = float(s.get("loop_period_us") or 100000) / 1e6
        bound = 0.5 + period_s + 0.001   # one loop after 0.5 s, +1 ms timestamp resolution
        safe = (not s.get("pump_on")) and float(s.get("valve_duty") or 0) <= 0.0
        self.measured = f"SENSOR_LOSS after {detect:.3f} s" + (f" · vent open {out:.3f} s" if out else "")
        self.detail = [f"Measured from the last valid PT-101 reading; window 0.5 < t ≤ {bound:.3f} s",
                       f"Trip {s.get('trip_reason')}: pump {'OFF' if not s.get('pump_on') else 'ON'}, "
                       f"vent {'OPEN' if safe else 'not open'}; latched (RESET only at the rig)"]
        self.extra = {"detect_s": detect, "out_s": out}
        ok = (s.get("trip_reason") == "SENSOR_LOSS" and 0.5 < detect <= bound and out is not None
              and out <= 1.0 and safe)
        self.verdict = "PASS" if ok else "FAIL"


class IS1Card(Card):
    def reset(self) -> None:
        super().reset()
        self.r = {k: {"rate": k, "warn_ms": None, "warn_pf": None, "t20_ms": None, "lead": None}
                  for k in (10, 20, 30)}

    def sample(self, s: dict) -> None:
        m = re.match(r"IS1 ramp (\d+)$", s.get("demo_phase") or "")
        if not m or int(m.group(1)) not in self.r:
            return
        r = self.r[int(m.group(1))]
        if r["t20_ms"] is not None:
            return
        if r["warn_ms"] is None and s.get("warning"):
            r["warn_ms"], r["warn_pf"] = float(s["t_esp_ms"]), _num(s.get("P_f"))
        p = _num(s.get("P_raw"))
        if s.get("valid") and p is not None and p >= 20.0:
            r["t20_ms"] = float(s["t_esp_ms"])
            if r["warn_ms"] is not None:
                r["lead"] = (r["t20_ms"] - r["warn_ms"]) / 1000.0

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        rows, txt = [], []
        for k, r in self.r.items():
            v = "PENDING"
            if r["t20_ms"] is not None:
                v = "PASS" if r["lead"] is not None and r["lead"] >= 10.0 else "FAIL"
            why = ("" if r["warn_pf"] is None else
                   " (14 mbar(g) threshold)" if r["warn_pf"] >= 14.0 else " (slope predictor)")
            rows.append({"rate": k, "lead": r["lead"], "verdict": v, "trigger": why.strip(" ()")})
            txt.append("--" if r["lead"] is None else f"{r['lead']:.1f}")
        self.extra = {"rows": rows}
        self.measured = "lead " + " / ".join(txt) + " s at 10 / 20 / 30 mbar/min"
        self.detail = [f"{x['rate']} mbar/min: " + ("--" if x["lead"] is None else
                       f"{x['lead']:.1f} s {x['verdict']}{' · ' + x['trigger'] if x['trigger'] else ''}")
                       for x in rows]
        vs = [x["verdict"] for x in rows]
        self.verdict = "FAIL" if "FAIL" in vs else "PASS" if all(v == "PASS" for v in vs) else "PENDING"


class IS2Card(Card):
    def reset(self) -> None:
        super().reset()
        self.t_d = None
        self.left = None
        self.back = None
        self.cand = None
        self.peak = None
        self.last_t = None

    def sample(self, s: dict) -> None:
        if (s.get("demo_mark") or "") == "disturbance":
            self.t_d = float(s["t_esp_ms"])
        if self.t_d is None or self.back is not None or s.get("replayed"):
            return
        p = _num(s.get("P_raw"))
        if p is None or not s.get("valid"):
            return
        t = float(s["t_esp_ms"])
        self.last_t = t
        self.peak = p if self.peak is None else max(self.peak, p)
        inside = abs(p - 10.0) <= IS2_BAND
        if not inside:
            self.cand = None
            if self.left is None:
                self.left = t
            return
        if self.cand is None:
            self.cand = t
        if t - self.cand >= IS2_CONFIRM_S * 1000.0:
            self.back = self.cand

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        if self.t_d is None:
            self.measured = "holding at 10 mbar(g); disturbance at 20 s…"
            return
        peak = "--" if self.peak is None else f"{self.peak:.2f}"
        if self.back is not None:
            rec = 0.0 if self.left is None else (self.back - self.t_d) / 1000.0
            self.measured = (f"back in 10 ± 2 after {rec:.1f} s" if self.left is not None
                             else "never left 10 ± 2")
            self.verdict = "PASS" if rec <= IS2_LIMIT_S else "FAIL"
            self.extra = {"recovery_s": rec}
        else:
            el = 0.0 if self.last_t is None else (self.last_t - self.t_d) / 1000.0
            self.measured = f"recovering… {el:.1f} s"
            self.verdict = "FAIL" if el > IS2_LIMIT_S + IS2_CONFIRM_S else "PENDING"
        self.detail = ["Disturbance: +4 mbar gas slug (R07) and inflow step 98.7 → 148 std cm³/min (P6)",
                       f"Peak PT-101 reading {peak} mbar(g)",
                       "Recovery measured from the disturbance to re-entry, confirmed 3 s inside the band"]


class C4Card(Card):
    def __init__(self, meta: dict, hosts: list[str]) -> None:
        self.hosts = hosts
        super().__init__(meta)

    def reset(self) -> None:
        super().reset()
        self.first_ms = None
        self.last_ms = None
        self.run_ok = True

    def sample(self, s: dict) -> None:
        if s.get("state") not in ("RUN", "IDLE", "BOOT"):
            self.run_ok = False
        if s.get("state") == "RUN":
            t = float(s["t_esp_ms"])
            self.first_ms = t if self.first_ms is None else self.first_ms
            self.last_ms = t

    def tick(self, demo_state: dict, running: bool) -> None:
        if not self.ran:
            return
        run_s = 0.0 if self.first_ms is None else (self.last_ms - self.first_ms) / 1000.0
        self.measured = f"{len(self.hosts)} external addresses in the code · control running {run_s:.0f} s"
        self.detail = ["PI, warning and every trip run on the controller; the dashboard only views and logs",
                       "Dashboard ↔ rig over the local link only (127.0.0.1 here, 192.168.4.1 on the rig)",
                       *([f"External: {h}" for h in self.hosts[:5]])]
        if self.hosts or not self.run_ok:
            self.verdict = "FAIL"
        elif run_s >= C4_MIN_RUN_S:
            self.verdict = "PASS"
        else:
            self.verdict = "PENDING"


class SpecBoard:
    def __init__(self) -> None:
        self.hosts = scan_external_hosts()
        meta = {c["id"]: c for c in CARDS}
        self.cards: dict[str, Card] = {
            "S1": S1Card(meta["S1"]), "S4": S4Card(meta["S4"]), "S5": S5Card(meta["S5"]),
            "S6a": TripCard(meta["S6a"]), "S6b": S6bCard(meta["S6b"]), "IS1": IS1Card(meta["IS1"]),
            "IS2": IS2Card(meta["IS2"]), "IS3": TripCard(meta["IS3"]), "C4": C4Card(meta["C4"], self.hosts),
        }
        self.demo_state: dict = {}

    def on_demo_state(self, m: dict) -> None:
        self.demo_state = m

    def on_sample(self, s: dict) -> None:
        sid = s.get("demo_scenario") or ""
        card = self.cards.get(sid)
        if card is None:
            return
        if (s.get("demo_mark") or "") == f"{sid} start":
            card.start()
        if card.ran:
            card.sample(s)

    def view(self) -> dict:
        running = self.demo_state.get("scenario") or ""
        out = []
        for cid in CARD_IDS:
            c = self.cards[cid]
            c.tick(self.demo_state, running == cid)
            out.append(c.view(running == cid))
        return {"type": "specs", "cards": out, "demo": self.demo_state}
