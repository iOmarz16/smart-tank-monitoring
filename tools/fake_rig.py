"""Fake rig: an ESP32 stand-in speaking exactly the firmware WebSocket protocol.

It runs a Python port of firmware/src/supervisor.cpp (constants are read from
firmware/include/config.h so they cannot drift), a simple headspace model, and
the same ring-buffer / replay / ping-pong behaviour as firmware/src/net_task.cpp.

Usage (from the repo root):
    dashboard\\.venv\\Scripts\\python tools\\fake_rig.py --scenario normal
    ... then start the dashboard with --rig ws://127.0.0.1:8765/

Scenarios:
    normal       START after 2 s, hold 10 mbar(g) at 98.7 std cm3/min
    ramp         vent blocked (P5-style) in RUN, 148 std cm3/min: warning at 14, trip at 20;
                 the block is released when the trip fires so the vent can act, and the
                 SOFT_LIMIT trip auto-clears back to RUN at 10 mbar(g) (A30)
    ramp_repeat  as ramp, but the vent is blocked again after every auto-clear: three
                 auto-clears, then the fourth SOFT_LIMIT trip within 5 min latches
    sensor_loss  hold at 10; PT-101 wire breaks at 60 s (reads 0 V), restored at 90 s
    wifi_outage  hold at 10; every client dropped and refused from 60 s for 60 s
    open_circuit PT-101 wire broken from boot (review C1): START is accepted (the
                 reading looks like a vented vessel) and the open-circuit check trips
                 SENSOR_LOSS 30 s later. Try it with --adc-floor-mv 120

Console (stdin) commands, like the USB serial port: ZERO [FORCE] START STOP RESET STATUS,
SIM RAMP <10|20|30>, SIM EXIT (A35; try --no-autostart then SIM RAMP 30),
LED MODE SAMPLE|VENT. TEST mode is not simulated (a trip from TEST always latches).
The simulated NVS holds a valid zero of 0.000 mbar at start (START allowed); use
--fresh-nvs to start like a first flash (START refused until ZERO).
This is a test tool: it is NOT a verified model of the rig.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

ROOT = Path(__file__).resolve().parents[1]
CONFIG_H = ROOT / "firmware" / "include" / "config.h"


# ---------------------------------------------------------------------------
# Constants from config.h
# ---------------------------------------------------------------------------
def load_config(path: Path = CONFIG_H) -> dict:
    cfg: dict[str, object] = {}
    pat = re.compile(r"constexpr\s+[\w:]+\s+(\w+)\s*=\s*([^;{]+);")
    for m in pat.finditer(path.read_text(encoding="utf-8")):
        name, raw = m.group(1), m.group(2).strip()
        raw = raw.rstrip("fF") if re.fullmatch(r"-?[\d.]+[fF]", raw) else raw
        if raw in ("true", "false"):
            cfg[name] = raw == "true"
            continue
        try:
            cfg[name] = int(raw)
        except ValueError:
            try:
                cfg[name] = float(raw)
            except ValueError:
                pass  # expressions are not needed here
    m = re.search(r"WS_PORT\s*=\s*(\d+)", path.read_text(encoding="utf-8"))
    if m:
        cfg["WS_PORT"] = int(m.group(1))
    return cfg


C = load_config()


# ---------------------------------------------------------------------------
# Supervisor: line-by-line port of firmware/src/supervisor.cpp
# ---------------------------------------------------------------------------
NAN = float("nan")


@dataclass
class Supervisor:
    zero_offset: float = 0.0
    zero_forced: bool = False
    zero_ok: bool = False          # valid non-forced zero in (simulated) NVS
    state: str = "BOOT"
    idle_since_us: int = 0
    reason: str = "NONE"
    read_ok: bool = False
    v_mv: float = 0.0
    pump_allowed_in_trip: bool = False
    trip_new: bool = False
    trip_latched: bool = False
    auto_clear_new: bool = False
    auto_clear_us: list = field(default_factory=list)  # times of recent auto-clears
    drop_rejected: bool = False
    pi_restart: bool = False
    trip_decision_us: int = 0
    trip_output_us: int = 0
    valid: bool = False
    has_ref: bool = False
    last_valid_p: float = 0.0
    last_valid_us: int = 0
    p_real: float = 0.0            # REAL sensor pressure (A35)
    p_raw: float = 0.0             # control pressure: = p_real, or the SIM pressure in SIM
    pf_real_init: bool = False
    p_f_real: float = 0.0
    sim_active: bool = False
    sim_rate: int = 0
    sim_p0: float = 0.0
    sim_t0_us: int = 0
    reref_count: int = 0
    reref_min: float = 0.0
    reref_max: float = 0.0
    plaus_low_since_us: int = 0
    zero_hist: deque = field(default_factory=lambda: deque(maxlen=int(C["ZERO_AVERAGE_SAMPLES"])))
    pf_init: bool = False
    p_f: float = 0.0
    win: deque = field(default_factory=lambda: deque(maxlen=int(C["SLOPE_WINDOW"])))
    slope: float = 0.0
    slope_ok: bool = False
    t_to_limit: float = 0.0
    t_to_limit_ok: bool = False
    warn_confirm: int = 0
    warning: bool = False
    integ: float = 0.0
    u_open: float = 100.0
    valve_duty: float = 0.0
    pump_on: bool = False

    def begin(self, now_us: int, zero_ok: bool) -> None:
        self.zero_ok = zero_ok and not self.zero_forced
        self.last_valid_us = now_us
        self.set_state("BOOT", now_us)

    def set_state(self, s: str, now_us: int) -> None:
        # Every state change restarts the IDLE dwell and clears the ZERO window.
        if s != self.state or s == "BOOT":
            self.idle_since_us = now_us
            self.zero_hist.clear()
        self.state = s

    def stale(self, now_us: int) -> bool:
        return now_us - self.last_valid_us > C["SENSOR_TIMEOUT_MS"] * 1000

    def validate(self, now_us: int, v_mv: float, read_ok: bool) -> None:
        self.valid = False
        self.drop_rejected = False
        self.read_ok, self.v_mv = read_ok, v_mv
        # ZERO window: consecutive valid IDLE samples only.
        kept = list(self.zero_hist)
        self.zero_hist.clear()
        if not read_ok:
            self.p_real = NAN
            self.reref_count = 0
            return
        p_uncorr = (v_mv / 1000.0) * C["SENSOR_DIVIDER_RATIO"] * C["SENSOR_MBAR_PER_V"]
        p = p_uncorr - self.zero_offset
        self.p_real = p
        if p < C["P_VALID_MIN"]:
            self.reref_count = 0
            return
        ok = True
        if self.has_ref and (self.last_valid_p - p) > C["P_MAX_DROP_PER_SAMPLE"]:
            ok = False
            self.drop_rejected = True
            if self.reref_count == 0:
                self.reref_min = self.reref_max = p
                self.reref_count = 1
            else:
                self.reref_min = min(self.reref_min, p)
                self.reref_max = max(self.reref_max, p)
                if self.reref_max - self.reref_min > C["REREF_MAX_SPREAD"]:
                    self.reref_min = self.reref_max = p
                    self.reref_count = 1
                else:
                    self.reref_count += 1
            if self.state == "TRIPPED" and self.reref_count >= C["REREF_SAMPLES"]:
                ok = True
        if not ok:
            return
        self.reref_count = 0
        self.valid = True
        self.has_ref = True
        self.last_valid_p = p
        self.last_valid_us = now_us
        if self.state == "IDLE":
            self.zero_hist.extend(kept)
            self.zero_hist.append(p_uncorr)
        a = C["PF_ALPHA"]
        if not self.pf_real_init:
            self.p_f_real, self.pf_real_init = p, True
        else:
            self.p_f_real = a * p + (1 - a) * self.p_f_real

    def update_control_pressure(self, now_us: int) -> None:
        # A35: control pressure = real (valid samples only, as before) or the SIM ramp.
        sim = self.state == "SIM"
        if self.sim_active and not sim:
            # Left SIM: back to the real pressure; the slope window restarts.
            self.p_f, self.pf_init = self.p_f_real, self.pf_real_init
            self.win.clear()
            self.warn_confirm = 0
            self.slope_ok = self.t_to_limit_ok = False
            self.warning = self.pf_init and self.p_f >= C["P_WARNING"]
        self.sim_active = sim
        if sim:
            t_s = ((now_us - self.sim_t0_us) // 1000) / 1000.0
            self.p_raw = self.sim_p0 + self.sim_rate / 60.0 * t_s
            self.push_control(now_us, self.p_raw)
            return
        self.p_raw = self.p_real
        if self.valid:
            self.push_control(now_us, self.p_real)

    def push_control(self, now_us: int, p: float) -> None:
        if not self.pf_init:
            self.p_f, self.pf_init = p, True
        else:
            a = C["PF_ALPHA"]
            self.p_f = a * p + (1 - a) * self.p_f
        self.win.append((now_us, self.p_f))
        self.update_warning()

    def update_warning(self) -> None:
        self.slope_ok = self.t_to_limit_ok = False
        n = C["SLOPE_WINDOW"]
        if len(self.win) == n:
            tn = self.win[-1][0]
            ts = [-(tn - t) / 1e6 for t, _ in self.win]
            ps = [p for _, p in self.win]
            st, sp = sum(ts), sum(ps)
            stt = sum(t * t for t in ts)
            stp = sum(t * p for t, p in zip(ts, ps))
            den = n * stt - st * st
            if den > 1e-9:
                self.slope = (n * stp - st * sp) / den
                self.slope_ok = True
        rule = False
        if self.slope_ok and self.slope > C["MIN_WARNING_SLOPE"]:
            self.t_to_limit = (C["P_SOFT_LIMIT"] - self.p_f) / self.slope
            self.t_to_limit_ok = True
            rule = self.t_to_limit <= C["WARN_HORIZON_S"]
        self.warn_confirm = self.warn_confirm + 1 if rule else 0
        self.warning = self.p_f >= C["P_WARNING"] or self.warn_confirm >= C["WARN_CONFIRM_SAMPLES"]

    def execute(self, cmd: str, now_us: int) -> str:
        ok = self.valid and not self.stale(now_us)
        if cmd in ("ZERO", "ZERO FORCE"):
            if self.state != "IDLE":
                return f"ERR ZERO only in IDLE, state={self.state}"
            idle_s = (now_us - self.idle_since_us) / 1e6
            if idle_s < C["ZERO_MIN_IDLE_S"]:
                return f"ERR ZERO needs {C['ZERO_MIN_IDLE_S']} s in IDLE; {idle_s:.0f} s so far"
            if not ok or len(self.zero_hist) < C["ZERO_AVERAGE_SAMPLES"]:
                return "ERR ZERO needs consecutive valid IDLE samples"
            spread = max(self.zero_hist) - min(self.zero_hist)
            if spread > C["ZERO_MAX_SPREAD"]:
                return f"ERR ZERO window not steady: max-min {spread:.3f} > {C['ZERO_MAX_SPREAD']}"
            z = sum(self.zero_hist) / len(self.zero_hist)
            if not abs(z) <= C["ZERO_FORCE_MAX_ABS_OFFSET"]:
                return f"ERR ZERO offset {z:.3f} exceeds the absolute cap (FORCE cannot override)"
            beyond = abs(z) > C["ZERO_MAX_ABS_OFFSET"]
            if beyond and cmd != "ZERO FORCE":
                return f"ERR ZERO offset {z:.3f} too large (ZERO FORCE overrides)"
            self.zero_offset = z
            self.zero_forced = beyond
            self.zero_ok = not beyond
            self.has_ref = self.pf_init = self.pf_real_init = False
            self.win.clear()
            self.warn_confirm = 0
            self.zero_hist.clear()
            if beyond:
                return f"WARN ZERO FORCE offset={z:.3f} mbar exceeds limit; zero_forced logged; START refused"
            return f"OK ZERO offset={z:.3f} mbar"
        if cmd == "START":
            if self.state != "IDLE":
                return f"ERR START only from IDLE, state={self.state}"
            if not ok:
                return "ERR START needs a valid sensor sample"
            if not self.zero_ok:
                why = "zero set with ZERO FORCE" if self.zero_forced else "no valid ZERO saved in NVS"
                return f"ERR START refused: {why}"
            self.set_state("RUN", now_us)
            self.integ, self.plaus_low_since_us = 0.0, 0
            self.pi_restart = False
            return "OK RUN"
        if cmd == "STOP":
            if self.state in ("RUN", "TEST", "SIM"):  # STOP also ends SIM (A35)
                self.set_state("IDLE", now_us)
                return "OK IDLE"
            if self.state == "TRIPPED":
                return f"ERR tripped ({self.reason}): STOP does not apply; RESET returns to IDLE"
            return f"OK already {self.state}"
        if cmd == "RESET":
            if self.state != "TRIPPED":
                return f"ERR not tripped, state={self.state}"
            if not ok:
                return "ERR RESET refused: sensor not valid"
            if not self.p_real < C["RESET_MAX_PRESSURE"]:  # always the REAL pressure (A35)
                return f"ERR RESET refused: P={self.p_real:.2f}"
            self.set_state("IDLE", now_us)
            self.reason = "NONE"
            self.pump_allowed_in_trip = False
            self.trip_latched = False
            self.trip_decision_us = self.trip_output_us = 0
            self.auto_clear_us.clear()
            return "OK RESET -> IDLE"
        if cmd == "STATUS":
            return (f"state={self.state} trip={self.reason} P_raw={self.p_raw:.3f} P_real={self.p_real:.3f} "
                    f"P_f={self.p_f:.3f} V_gpio={self.v_mv:.1f}mV u_open={self.u_open:.1f} "
                    f"integ={self.integ:.3f} zero={self.zero_offset:.3f} zero_ok={int(self.zero_ok)} "
                    f"latched={int(self.state == 'TRIPPED' and self.trip_latched)} "
                    f"auto_clears={self.auto_clears_in_window(now_us)}/{C['SOFT_AUTOCLEAR_MAX']}")
        if cmd.startswith("SIM RAMP"):
            # A35: gate as START (A24); from IDLE or TEST.
            parts = cmd.split()
            rate = int(parts[2]) if len(parts) == 3 and parts[2].isdigit() else -1
            if self.state not in ("IDLE", "TEST"):
                return f"ERR SIM RAMP only from IDLE or TEST, state={self.state}"
            if rate not in (10, 20, 30):
                return "ERR SIM RAMP rate must be 10, 20 or 30 mbar/min"
            if not ok or not self.pf_real_init:
                return "ERR SIM RAMP needs a valid sensor sample"
            if not self.zero_ok:
                return "ERR SIM RAMP refused: no valid ZERO saved in NVS"
            self.set_state("SIM", now_us)
            self.sim_rate, self.sim_p0, self.sim_t0_us = rate, self.p_f_real, now_us
            self.integ, self.pi_restart = 0.0, False
            return f"OK SIM RAMP {rate} mbar/min from {self.sim_p0:.2f} mbar(g)"
        if cmd == "SIM EXIT":
            if self.state == "SIM":
                self.set_state("IDLE", now_us)
                return "OK IDLE (SIM ended: pump OFF, valve released)"
            return f"ERR not in SIM, state={self.state}"
        return "ERR unknown command (fake rig: ZERO [FORCE] START STOP RESET STATUS SIM RAMP <n> SIM EXIT LED MODE)"

    def auto_clears_in_window(self, now_us: int) -> int:
        w = C["SOFT_AUTOCLEAR_WINDOW_S"] * 1_000_000
        return sum(1 for t in self.auto_clear_us[-C["SOFT_AUTOCLEAR_MAX"]:] if now_us - t <= w)

    def enter_trip(self, r: str, clock_us: int) -> None:
        was_on, came_from = self.pump_on, self.state
        self.set_state("TRIPPED", clock_us)
        self.reason, self.trip_new = r, True
        self.trip_decision_us, self.trip_output_us = clock_us, 0
        self.pump_allowed_in_trip = (not C["PUMP_OFF_AT_SOFT_LIMIT"]) and r == "SOFT_LIMIT" and was_on
        # A30: only a SOFT_LIMIT trip from RUN, with quota left, may auto-clear.
        self.trip_latched = not (r == "SOFT_LIMIT" and came_from == "RUN"
                                 and self.auto_clears_in_window(clock_us) < C["SOFT_AUTOCLEAR_MAX"])

    def auto_clear(self, now_us: int) -> None:
        # Bumpless: this step's PI update gives exactly u_open (= 100 in TRIPPED).
        self.auto_clear_us = (self.auto_clear_us + [now_us])[-C["SOFT_AUTOCLEAR_MAX"]:]
        self.set_state("RUN", now_us)
        self.reason = "NONE"
        self.pump_allowed_in_trip = self.trip_latched = False
        self.trip_decision_us = self.trip_output_us = 0
        self.plaus_low_since_us = 0
        e = self.p_f - C["SETPOINT"]
        self.integ = (self.u_open / C["KC"] - e) * (C["TI_S"] / C["TS_S"])
        self.pi_restart = True
        self.auto_clear_new = True

    def evaluate_trip(self, now_us: int, clock_us: int) -> None:
        # Open-circuit check: raw pin mV, armed whenever the pump is commanded ON (any state).
        plaus_fail = False
        if self.pump_on:
            if self.read_ok:
                if self.v_mv < C["OPEN_CIRCUIT_MV_THRESHOLD"]:
                    if self.plaus_low_since_us == 0:
                        self.plaus_low_since_us = now_us
                else:
                    self.plaus_low_since_us = 0
            if self.plaus_low_since_us and now_us - self.plaus_low_since_us > C["PLAUS_TIMEOUT_MS"] * 1000:
                plaus_fail = True
        else:
            self.plaus_low_since_us = 0
        # A35: limits on the control pressure AND, always, on the REAL pressure.
        sim = self.sim_active
        ctrl_ok = sim or self.valid
        hard = ((self.valid and self.p_real >= C["P_HARD_LIMIT"])
                or (sim and self.p_raw >= C["P_HARD_LIMIT"]))
        p_soft = self.p_raw
        if C["LAG_COMP_ENABLED"] and self.slope_ok:
            p_soft = max(self.p_raw, self.p_raw + C["LAG_COMP_S"] * self.slope)
        soft = ((ctrl_ok and p_soft >= C["P_SOFT_LIMIT"])
                or (self.valid and self.p_real >= C["P_SOFT_LIMIT"]))
        loss = self.stale(now_us) or plaus_fail
        r = "HARD_LIMIT" if hard else "SOFT_LIMIT" if soft else "SENSOR_LOSS" if loss else "NONE"
        if self.state != "TRIPPED":
            if r != "NONE":
                self.enter_trip(r, clock_us)
            return
        if self.reason == "SOFT_LIMIT" and (hard or loss or self.drop_rejected):
            self.pump_allowed_in_trip = False
            self.trip_latched = True
            self.reason = "HARD_LIMIT" if hard else "SENSOR_LOSS"
            return
        if (self.reason == "SOFT_LIMIT" and not self.trip_latched and r == "NONE" and self.valid
                and not self.stale(now_us) and self.zero_ok and self.p_real <= C["SOFT_AUTOCLEAR_P"]):
            self.auto_clear(now_us)

    def compute_outputs(self) -> tuple[bool, float]:
        pump, duty = False, 0.0
        if self.state in ("BOOT", "IDLE"):
            self.u_open = 100.0
        elif self.state == "TRIPPED":
            self.u_open = 100.0
            pump = self.pump_allowed_in_trip
        elif self.state in ("RUN", "SIM"):  # A35: in SIM the PI is computed and logged only
            pump = True
            if self.valid:
                e = self.p_f - C["SETPOINT"]
                k = C["TS_S"] / C["TI_S"]
                if self.pi_restart:
                    self.pi_restart = False  # A30 bumpless restart: integ already set
                else:
                    s = self.integ + e
                    u_cand = C["KC"] * (e + k * s)
                    if (u_cand > 100 and e > 0) or (u_cand < 0 and e < 0):
                        s = self.integ
                    self.integ = s
                self.u_open = min(100.0, max(0.0, C["KC"] * (e + k * self.integ)))
            duty = 100.0 - self.u_open
            if self.state == "SIM":
                duty = 100.0  # A35: PCV-101 energized (closed) for the whole SIM run
        self.pump_on = pump
        self.valve_duty = max(0.0, duty)
        return pump, self.valve_duty

    def step(self, now_us: int, v_mv: float, read_ok: bool, cmd: str | None, clock_us: int):
        self.trip_new = False
        self.auto_clear_new = False
        self.validate(now_us, v_mv, read_ok)
        self.update_control_pressure(now_us)
        if self.state == "BOOT" and self.valid:
            self.set_state("IDLE", now_us)
        reply = self.execute(cmd, now_us) if cmd else None
        if self.state == "SIM" and now_us - self.sim_t0_us > C["TEST_PUMP_MAX_S"] * 1_000_000:
            self.set_state("IDLE", now_us)  # A35 backstop (not reached: the ramp trips first)
        self.evaluate_trip(now_us, clock_us)
        pump, duty = self.compute_outputs()
        return pump, duty, reply


# ---------------------------------------------------------------------------
# Team leak model: line-by-line port of firmware/src/leak_model.h (A34).
# LOG AND DISPLAY ONLY. P_comp is an ASSUMPTION, not the vessel pressure. The
# Supervisor above never sees it (tools/check_rules.py checks this).
# ---------------------------------------------------------------------------
class LeakModel:
    def __init__(self) -> None:
        self.lost = 0.0  # mbar added so far

    def update(self, p_f: float, pump_on: bool, closed_duty: float) -> tuple[float, float, float]:
        """p_f of this sample; pump_on / closed_duty = outputs written in the PREVIOUS loop.
        Returns (p_corr, p_comp, leak_rate)."""
        if math.isnan(p_f):
            return NAN, NAN, NAN
        p_corr = p_f + (C["PUMP_ON_OFFSET"] if pump_on else 0.0)
        k = C["K_LEAK_PUMP_ON"] if pump_on else C["K_LEAK_PUMP_OFF"]
        leak_rate = k * math.sqrt(p_corr) if p_corr > 0.0 else 0.0
        if not pump_on and p_corr < C["P_COMP_RESET_P"]:
            self.lost = 0.0  # vessel empty -> reset
        frac = closed_duty / 100.0
        frac = 0.0 if not frac > 0.0 else min(frac, 1.0)
        self.lost += leak_rate * C["TS_S"] * frac
        return p_corr, p_corr + self.lost, leak_rate


# ---------------------------------------------------------------------------
# Plant: 5.0 L headspace, C = 5.20 std cm3/mbar (Section 5.2.1),
# open vent passes 250 std cm3/min at 10 mbar(g) (HYSYS calibration).
# ---------------------------------------------------------------------------
@dataclass
class Plant:
    p: float = 0.0             # true pressure, mbar(g)
    p_sensor: float = 0.0      # after the ~1 s QBM2030 lag
    inflow: float = 98.7       # std cm3/min while the pump runs
    cap: float = 5.20          # std cm3/mbar
    k_vent: float = 25.0       # std cm3/min per mbar at full open
    tau_s: float = 1.0
    noise: float = 0.10
    vent_blocked: bool = False
    wire_broken: bool = False
    adc_floor_mv: float = 0.0  # ESP32 11 dB: a 0 V input reads ~75-150 mV (review C1)

    def step(self, dt: float, pump: bool, valve_duty: float) -> None:
        q_in = self.inflow if pump else 0.0
        open_frac = 0.0 if self.vent_blocked else (100.0 - valve_duty) / 100.0
        q_out = self.k_vent * max(self.p, 0.0) * open_frac
        self.p += (q_in - q_out) / self.cap / 60.0 * dt
        self.p_sensor += (self.p - self.p_sensor) * min(1.0, dt / self.tau_s)

    def v_gpio_mv(self, zero_true: float) -> float:
        if self.wire_broken:
            return self.adc_floor_mv
        p = self.p_sensor + zero_true + random.gauss(0.0, self.noise)
        v_sensor = min(max(0.0, p) / C["SENSOR_MBAR_PER_V"], 10.0)  # QBM2030 output 0-10 V
        v = v_sensor / C["SENSOR_DIVIDER_RATIO"] * 1000.0            # /5 module: <= 2.0 V
        return max(v, self.adc_floor_mv)  # ADC floor at 11 dB


# ---------------------------------------------------------------------------
# Rig: 10 Hz loop + ring buffer + WebSocket server
# ---------------------------------------------------------------------------
class Rig:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.t0 = time.perf_counter()
        # Rig clock: ms since "boot", deliberately unrelated to the PC clock, with drift.
        self.boot_offset_ms = random.uniform(5_000, 50_000)
        self.sup = Supervisor()
        self.plant = Plant(noise=args.noise, adc_floor_mv=args.adc_floor_mv)
        self.zero_true = args.zero_error  # sensor zero error that ZERO removes
        self.ring: deque = deque(maxlen=int(C["RING_SAMPLES"]))
        self.seq = 0
        self.cmd_queue: deque[str] = deque()
        self.wifi_down = False
        self.led_mode = "VENT"   # LED_MODE_DEFAULT
        self.led_on = False
        self.t3: tuple[int, str] = (0, "NONE")
        # Leak model (A34), display/log only; inputs are the previous loop's outputs.
        self.leak = LeakModel()
        self.model_pump_on = False
        self.model_closed_duty = 0.0
        self.clients: set[ServerConnection] = set()
        self.new_sample = asyncio.Event()

    def clock_us(self) -> int:
        el = time.perf_counter() - self.t0
        return int((el * (1 + self.args.drift_ppm * 1e-6)) * 1e6 + self.boot_offset_ms * 1000)

    def esp_ms(self) -> float:
        return self.clock_us() / 1000.0

    # --- scenario timeline -------------------------------------------------
    def scenario(self, t: float) -> None:
        sc = self.args.scenario
        if t >= 2.0 and not getattr(self, "_started", False):
            self._started = True
            if sc in ("ramp", "ramp_repeat"):
                self.plant.inflow = 148.0
                self.plant.vent_blocked = True
            if not self.args.no_autostart:
                self.cmd_queue.append("START")
        if sc in ("ramp", "ramp_repeat") and self.plant.vent_blocked and self.sup.state == "TRIPPED":
            self.plant.vent_blocked = False
        if sc == "ramp_repeat" and self.sup.auto_clear_new:
            self.plant.vent_blocked = True
        if sc == "sensor_loss":
            self.plant.wire_broken = 60.0 <= t < 90.0
        if sc == "open_circuit":
            self.plant.wire_broken = True
        if sc == "wifi_outage":
            down = self.args.outage_at <= t < self.args.outage_at + self.args.outage_len
            if down and not self.wifi_down:
                print(f"[fake_rig] Wi-Fi DOWN at t={t:.1f} s", flush=True)
                for ws in list(self.clients):
                    ws.transport.abort()  # abrupt, like a lost radio link
            if not down and self.wifi_down:
                print(f"[fake_rig] Wi-Fi UP at t={t:.1f} s", flush=True)
            self.wifi_down = down

    # --- 10 Hz control loop ---------------------------------------------------
    async def control_loop(self) -> None:
        period = C["CONTROL_PERIOD_MS"] / 1000.0
        # Simulated NVS: a valid zero of 0.000 mbar, unless --fresh-nvs (first flash).
        self.sup.begin(self.clock_us(), zero_ok=not self.args.fresh_nvs)
        prev_us = self.clock_us()
        next_t = time.perf_counter()
        while True:
            next_t += period
            # Windows timers are ~15.6 ms coarse: sleep most of the way, then spin,
            # so the fake loop period is as steady as the ESP32's vTaskDelayUntil.
            await asyncio.sleep(max(0.0, next_t - time.perf_counter() - 0.02))
            while time.perf_counter() < next_t:
                pass
            now_us = self.clock_us()
            t = time.perf_counter() - self.t0
            self.scenario(t)
            self.plant.step(period, self.sup.pump_on, self.sup.valve_duty)
            v = self.plant.v_gpio_mv(self.zero_true)
            if self.led_mode == "SAMPLE":
                self.led_on = not self.led_on  # toggles at t0, like the firmware
            cmd = self.cmd_queue.popleft() if self.cmd_queue else None
            reply = None
            if cmd and cmd.startswith("LED MODE "):
                mode = cmd.split()[-1]
                if mode in ("SAMPLE", "VENT"):
                    self.led_mode = mode
                    reply = f"OK LED MODE {mode}"
                else:
                    reply = "ERR LED MODE SAMPLE|VENT"
                cmd = None
            _pump, _duty, sreply = self.sup.step(now_us, v, True, cmd, self.clock_us())
            reply = reply or sreply
            if self.sup.trip_new:
                self.sup.trip_output_us = self.clock_us()
                if C["READBACK_ENABLED"]:
                    # simulated MOSFET OFF edge: 2-6 ms valve response (Table C)
                    self.t3 = (self.sup.trip_output_us + random.randint(2000, 6000), "READBACK")
                else:
                    self.t3 = (self.sup.trip_output_us, "SOFTWARE")
            if self.sup.state != "TRIPPED":
                self.t3 = (0, "NONE")
            if self.led_mode == "VENT":
                self.led_on = self.sup.valve_duty <= 0.0
            if reply:
                print(f"[fake_rig] {cmd or 'LED MODE'} -> {reply}", flush=True)
            s = self.sup
            # Leak model (A34) AFTER the supervisor step, like control_task.cpp.
            p_corr, p_comp, leak_rate = self.leak.update(
                s.p_f_real if s.pf_real_init else NAN, self.model_pump_on, self.model_closed_duty)
            self.model_pump_on, self.model_closed_duty = s.pump_on, s.valve_duty
            r4 = lambda x, n: None if math.isnan(x) else round(x, n)  # noqa: E731
            sample = {
                "type": "sample", "seq": self.seq, "t_esp_ms": now_us // 1000,
                "P_raw": None if math.isnan(s.p_raw) else round(s.p_raw, 4),
                "P_f": round(s.p_f, 4) if s.pf_init else None,
                "P_real": None if math.isnan(s.p_real) else round(s.p_real, 4),
                "simulated": s.sim_active, "sim_rate": s.sim_rate if s.sim_active else 0,
                "valid": s.valid,
                "u_open": round(s.u_open, 4), "integ": round(s.integ, 5),
                "valve_duty": round(s.valve_duty, 4), "pump_on": s.pump_on, "warning": s.warning,
                "t_to_limit": round(s.t_to_limit, 2) if s.t_to_limit_ok else None,
                "state": s.state, "trip": s.state == "TRIPPED", "trip_reason": s.reason,
                "loop_period_us": now_us - prev_us, "replayed": False,
                "slope": round(s.slope, 4) if s.slope_ok else None,
                "v_gpio_mv": round(v, 1), "sensor_fault": (not s.valid) and not s.stale(now_us),
                "zero_offset": round(s.zero_offset, 4), "zero_forced": s.zero_forced,
                "trip_decision_us": s.trip_decision_us, "trip_output_us": s.trip_output_us,
                "t3_us": self.t3[0], "t3_source": self.t3[1], "led_on": self.led_on,
                "rb_valve_off": bool(C["READBACK_ENABLED"]) and s.valve_duty <= 0.0,
                "rb_pump_off": bool(C["READBACK_ENABLED"]) and not s.pump_on,
                "reset_reason": "POWERON",
                "trip_latched": s.state == "TRIPPED" and s.trip_latched,
                "auto_clears": s.auto_clears_in_window(now_us),
                "P_corr": r4(p_corr, 4), "P_comp_assumed": r4(p_comp, 2),
                "leak_rate": r4(leak_rate, 4),
            }
            if s.auto_clear_new:
                print(f"[fake_rig] EVENT AUTO-CLEAR SOFT_LIMIT -> RUN P_raw={s.p_raw:.3f} "
                      f"auto_clears={sample['auto_clears']}/{C['SOFT_AUTOCLEAR_MAX']}", flush=True)
            if self.sup.trip_new:
                print(f"[fake_rig] EVENT TRIPPED {s.reason} "
                      f"{'LATCHED (RESET required)' if s.trip_latched else 'auto-clears at P <= 10'}",
                      flush=True)
            prev_us = now_us
            self.ring.append(sample)
            self.seq += 1
            if self.seq % 50 == 0 or self.sup.trip_new:
                print(f"[fake_rig] t={t:6.1f}s seq={self.seq} state={s.state} P={s.p_raw:6.2f} "
                      f"P_true={self.plant.p:6.2f} P_comp_assumed={p_comp:6.2f} "
                      f"u_open={s.u_open:5.1f} warn={s.warning} "
                      f"trip={s.reason} clients={len(self.clients)}", flush=True)
            self.new_sample.set()

    # --- WebSocket ------------------------------------------------------------
    def process_request(self, connection, request):
        if self.wifi_down:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Wi-Fi down (simulated)\n")
        return None

    async def handler(self, ws: ServerConnection) -> None:
        # Same policy as net_task.cpp: live samples first, backlog interleaved at REPLAY_RATE_HZ.
        self.clients.add(ws)
        replaying = bool(self.ring)
        replay_next = self.ring[0]["seq"] if self.ring else 0
        replay_end = self.ring[-1]["seq"] if self.ring else -1
        live_next = replay_end + 1
        tokens, last = 0.0, time.perf_counter()
        rate = C["REPLAY_RATE_HZ"]
        print(f"[fake_rig] client connected; backlog {replay_end - replay_next + 1} samples", flush=True)

        async def send(seq: int, replayed: bool) -> None:
            oldest = self.ring[0]["seq"]
            if seq < oldest:
                return
            s = dict(self.ring[seq - oldest])
            s["replayed"] = replayed
            await ws.send(json.dumps(s, separators=(",", ":")))

        async def reader() -> None:
            async for msg in ws:
                try:
                    m = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                if not isinstance(m, dict) or m.get("type") != "ping":
                    continue  # the only accepted inbound message
                t_pc = m.get("t_pc")
                if not isinstance(t_pc, (int, float)) or isinstance(t_pc, bool):
                    continue
                await self.net_delay()
                await ws.send(json.dumps({"type": "pong", "t_pc": t_pc, "t_esp": round(self.esp_ms(), 3)}))

        rtask = asyncio.create_task(reader())
        try:
            while True:
                if not self.ring:
                    await asyncio.sleep(0.01)
                    continue
                did = False
                # 1) live samples first, always
                if live_next <= self.ring[-1]["seq"]:
                    await self.net_delay()
                    burst = 0
                    while live_next <= self.ring[-1]["seq"] and burst < C["NET_SEND_BURST"]:
                        late = self.ring[-1]["seq"] - live_next >= C["REPLAY_LAG_SAMPLES"]
                        await send(live_next, late)
                        live_next += 1
                        burst += 1
                    did = True
                # 2) backlog, rate-limited token bucket
                now = time.perf_counter()
                tokens = min(2.0, tokens + (now - last) * rate)
                last = now
                if replaying and tokens >= 1.0:
                    await self.net_delay()
                    while replaying and tokens >= 1.0:
                        replay_next = max(replay_next, self.ring[0]["seq"])
                        if replay_next > replay_end:
                            replaying = False
                            print("[fake_rig] backlog replay complete", flush=True)
                            break
                        await send(replay_next, True)
                        replay_next += 1
                        tokens -= 1.0
                    did = True
                if not did:
                    self.new_sample.clear()
                    try:
                        await asyncio.wait_for(self.new_sample.wait(), 0.01 if replaying else 0.2)
                    except TimeoutError:
                        pass
        except ConnectionClosed:
            pass
        finally:
            rtask.cancel()
            self.clients.discard(ws)
            print("[fake_rig] client disconnected", flush=True)

    async def net_delay(self) -> None:
        d = self.args.net_delay_ms + random.expovariate(1.0 / max(self.args.net_jitter_ms, 0.001))
        await asyncio.sleep(d / 1000.0)

    # --- console --------------------------------------------------------------
    def console_thread(self, loop: asyncio.AbstractEventLoop) -> None:
        for line in sys.stdin:
            cmd = line.strip().upper()
            if cmd:
                loop.call_soon_threadsafe(self.cmd_queue.append, cmd)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        threading.Thread(target=self.console_thread, args=(loop,), daemon=True).start()
        async with serve(self.handler, self.args.host, self.args.port,
                         process_request=self.process_request, ping_interval=None,
                         max_queue=64):
            print(f"[fake_rig] scenario={self.args.scenario} listening on "
                  f"ws://{self.args.host}:{self.args.port}/  (type START/STOP/RESET/ZERO/STATUS)",
                  flush=True)
            if self.args.duration > 0:
                try:
                    await asyncio.wait_for(self.control_loop(), self.args.duration)
                except TimeoutError:
                    print("[fake_rig] duration reached, exiting", flush=True)
            else:
                await self.control_loop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=["normal", "ramp", "ramp_repeat", "sensor_loss", "wifi_outage",
                                           "open_circuit"],
                    default="normal")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--noise", type=float, default=0.10, help="sensor noise, mbar (1 sigma)")
    ap.add_argument("--net-delay-ms", type=float, default=4.0, help="one-way base delay")
    ap.add_argument("--net-jitter-ms", type=float, default=6.0, help="mean extra delay (exponential)")
    ap.add_argument("--drift-ppm", type=float, default=30.0, help="rig clock drift vs PC")
    ap.add_argument("--outage-at", type=float, default=60.0)
    ap.add_argument("--outage-len", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--zero-error", type=float, default=0.15, help="sensor zero error, mbar (ZERO removes it)")
    ap.add_argument("--no-autostart", action="store_true", help="stay in IDLE (type START)")
    ap.add_argument("--fresh-nvs", action="store_true",
                    help="no zero in NVS, like a first flash: START refused until ZERO (after 60 s in IDLE)")
    ap.add_argument("--adc-floor-mv", type=float, default=0.0,
                    help="ESP32 ADC floor: readings below it read as it (real 11 dB: ~75-150 mV)")
    ap.add_argument("--duration", type=float, default=0.0, help="exit after N s (0 = run forever)")
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    try:
        asyncio.run(Rig(args).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
