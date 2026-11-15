"""Regression checks of the safety rules (review C1/M3, A29 scaling, A30 SOFT_LIMIT
auto-clear) on the fake_rig Python port of supervisor.cpp.

Usage: python tools/check_rules.py   (exit code 0 = all pass)

This tests the SIMULATOR'S COPY of the logic in tools/fake_rig.py, not the C++ firmware.
Keep the two in step: a change to supervisor.cpp needs the same change in fake_rig.py.
TEST mode (including the TEST PUMP ON <seconds> limit) is not simulated, so it is not
covered here; check it on the bench."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_rig as fr  # noqa: E402

C = fr.C
MV_PER_MBAR = 1000.0 / (C["SENSOR_DIVIDER_RATIO"] * C["SENSOR_MBAR_PER_V"])
DT = 100_000
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    fails += 0 if cond else 1


class Run:
    def __init__(self, zero_ok, zero=0.0, forced=False):
        self.s = fr.Supervisor(zero_offset=zero, zero_forced=forced)
        self.t = 1_000_000
        self.s.begin(self.t, zero_ok)

    def step(self, mv, cmd=None) -> str:
        self.t += DT
        _, _, reply = self.s.step(self.t, mv, True, cmd, self.t)
        return reply or ""  # no command -> no reply

    def hold(self, mv, seconds):
        for _ in range(int(seconds * 10)):
            self.step(mv)

    def to(self, p_to, rate=0.15):
        """Move the reading in mbar steps <= rate per sample (below the 2 mbar drop check)."""
        p = self.s.p_raw + self.s.zero_offset
        while abs(p_to - p) > 1e-9:
            p += max(-rate, min(rate, p_to - p))
            self.step(p * MV_PER_MBAR)

    def run_at(self, p):
        self.hold(p * MV_PER_MBAR, 1)
        self.step(p * MV_PER_MBAR, "START")
        assert self.s.state == "RUN", self.s.state

    def soft_trip_and_clear(self):
        """From RUN: rise to 20.5 (SOFT_LIMIT trip), fall to 10.0. Returns (tripped, latched)."""
        self.to(20.5)
        tripped, latched = self.s.state == "TRIPPED", self.s.trip_latched
        self.to(9.9)
        return tripped, latched


# 1. First flash (no NVS zero): START refused; ZERO refused before 60 s in IDLE.
r = Run(zero_ok=False)
r.hold(100.0, 1)  # open-circuit-like reading, ADC floor 100 mV
check("BOOT -> IDLE on valid sample", r.s.state == "IDLE")
rep = r.step(100.0, "START")
check("START refused without NVS zero", r.s.state == "IDLE" and "refused" in rep, rep)
rep = r.step(100.0, "ZERO")
check("ZERO refused before 60 s IDLE", "60 s" in rep, rep)
r.hold(100.0, 60)
rep = r.step(100.0, "ZERO")
check("ZERO accepted after 60 s steady IDLE", rep.startswith("OK ZERO"), rep)
check("zero_ok after ZERO", r.s.zero_ok)

# 2. C1 core case: never zeroed (offset 0), ADC floor 120 mV = +1.55 mbar(g), wire broken.
r = Run(zero_ok=True, zero=0.0)
r.hold(120.0, 1)
check("open circuit reads P_raw > 1.0 (old check blind)", r.s.p_raw > 1.0, f"P_raw={r.s.p_raw:.2f}")
r.step(120.0, "START")
check("START accepted (looks vented)", r.s.state == "RUN")
r.hold(120.0, 29.5)
check("no trip before 30 s", r.s.state == "RUN")
r.hold(120.0, 1.0)
check("SENSOR_LOSS after 30 s pump ON below threshold", r.s.state == "TRIPPED" and r.s.reason == "SENSOR_LOSS",
      f"{r.s.state}/{r.s.reason}")
check("pump OFF in trip", not r.s.pump_on)

# 3. Healthy slowest ramp (10 mbar/min from 0) must NOT trip the open-circuit check.
r = Run(zero_ok=True, zero=0.0)
r.hold(120.0, 1)
r.step(120.0, "START")
p = 0.0
for _ in range(400):  # 40 s
    p += 10.0 / 60.0 * 0.1
    r.step(max(120.0, p * MV_PER_MBAR))
check(f"10 mbar/min ramp passes {C['OPEN_CIRCUIT_MV_THRESHOLD']:.0f} mV before 30 s", r.s.state == "RUN", f"{r.s.state}/{r.s.reason}")

# 3b. A25 measured threshold (bench, reported 2026-11-16): open circuit reads 142.0 mV steady,
#     147.7 mV max; rig zero offset 2.13 mbar; pump-ON plateau P_raw 2.6 = 314 mV.
check("A25 threshold = measured 180 mV, above the 147.7 mV open-circuit max, <= 300 mV",
      C["OPEN_CIRCUIT_MV_THRESHOLD"] == 180.0 and 147.7 < C["OPEN_CIRCUIT_MV_THRESHOLD"] <= 300.0)
r = Run(zero_ok=True, zero=2.13)
r.hold(142.0, 1)
r.step(142.0, "START")  # open circuit looks like a vented vessel (P_raw ~ 0)
r.hold(147.7, 31)       # worst-case open-circuit noise for the whole window
check("A25 open circuit at the 147.7 mV max trips SENSOR_LOSS after 30 s",
      r.s.state == "TRIPPED" and r.s.reason == "SENSOR_LOSS", f"{r.s.state}/{r.s.reason}")
r = Run(zero_ok=True, zero=2.13)
r.hold(142.0, 1)
r.step(142.0, "START")
r.to(2.6 + 2.13, rate=0.05)  # pump plateau with the vent closed: P_raw 2.6 = 314 mV
r.hold(314.0, 300)           # the team's 300 s pump-ON run
check("A25 healthy pump plateau (314 mV) runs 300 s without SENSOR_LOSS", r.s.state == "RUN",
      f"{r.s.state}/{r.s.reason}")

# 4. M3: ZERO only from steady IDLE samples, FORCE capped.
r = Run(zero_ok=True)
r.hold(2.0 * MV_PER_MBAR, 61)
for i in range(10):  # blow-down tail: falling 0.05 mbar/sample -> spread 0.45
    r.step((2.0 - 0.05 * i) * MV_PER_MBAR)
rep = r.step(1.5 * MV_PER_MBAR, "ZERO")
check("ZERO refused on unsteady window", "not steady" in rep, rep)
r = Run(zero_ok=True)
r.hold(4.0 * MV_PER_MBAR, 61)
rep = r.step(4.0 * MV_PER_MBAR, "ZERO")
check("ZERO refused at 4 mbar (> 3)", "too large" in rep, rep)
rep = r.step(4.0 * MV_PER_MBAR, "ZERO FORCE")
check("ZERO FORCE accepted at 4 mbar", rep.startswith("WARN"), rep)
check("forced zero makes START refused", not r.s.zero_ok)
rep = r.step(4.0 * MV_PER_MBAR, "START")
check("START refused after ZERO FORCE", "refused" in rep, rep)
r = Run(zero_ok=True)
r.hold(6.0 * MV_PER_MBAR, 61)
rep = r.step(6.0 * MV_PER_MBAR, "ZERO FORCE")
check("ZERO FORCE refused at 6 mbar (cap 5)", "absolute cap" in rep, rep)

# 5. ZERO window excludes RUN samples and restarts on STOP.
r = Run(zero_ok=True)
r.hold(800.0, 1)
r.step(800.0, "START")
r.hold(800.0, 5)
r.step(800.0, "STOP")
rep = r.step(800.0, "ZERO")
check("ZERO right after STOP refused (dwell restarted)", "60 s" in rep, rep)

# 6. A29 scaling: /5 module, P = mV x 0.015 - zero; 10 V sensor = 2.0 V at the pin.
r = Run(zero_ok=True, zero=0.2)
r.step(1000.0)
check("1000 mV -> 15.0 mbar(g) minus zero", abs(r.s.p_raw - (15.0 - 0.2)) < 1e-9, f"{r.s.p_raw:.4f}")
check("10 V sensor = 2000 mV at the pin", abs(fr.Plant(noise=0.0, p_sensor=30.0).v_gpio_mv(0.0) - 2000.0) < 1e-6)

# 7. A30: SOFT_LIMIT trip from RUN auto-clears at P <= 10 with a bumpless PI restart.
r = Run(zero_ok=True)
r.run_at(10.0)
r.to(20.5)
check("SOFT_LIMIT trip from RUN, not latched", r.s.state == "TRIPPED" and r.s.reason == "SOFT_LIMIT"
      and not r.s.trip_latched, f"{r.s.state}/{r.s.reason}/latched={r.s.trip_latched}")
check("pump OFF and vent fully open in the trip", not r.s.pump_on and r.s.u_open == 100.0)
r.to(10.2)
check("still TRIPPED at 10.2 (> 10.0)", r.s.state == "TRIPPED")
r.step(10.05 * MV_PER_MBAR)
check("still TRIPPED at 10.05", r.s.state == "TRIPPED")
r.step(9.99 * MV_PER_MBAR)  # exactly 10.0 does not survive the mV round trip in floats
check("auto-clear at P_raw <= 10.0 -> RUN", r.s.state == "RUN" and r.s.auto_clear_new, r.s.state)
check("bumpless: u_open = 100.0 at the clear sample", abs(r.s.u_open - 100.0) < 1e-3, f"{r.s.u_open:.4f}")
check("pump ON again after the auto-clear", r.s.pump_on)
u0 = r.s.u_open
r.step(10.0 * MV_PER_MBAR)
check("no output jump on the next sample (< 2 %)", abs(r.s.u_open - u0) < 2.0, f"{u0:.2f} -> {r.s.u_open:.2f}")
check("auto-clear counted (1/3)", r.s.auto_clears_in_window(r.t) == 1)

# 8. A30: after 3 auto-clears within 5 min, the next SOFT_LIMIT trip latches.
r = Run(zero_ok=True)
r.run_at(10.0)
ok3 = all(r.soft_trip_and_clear() == (True, False) and r.s.state == "RUN" for _ in range(3))
check("three SOFT_LIMIT trips auto-clear", ok3, f"auto_clears={r.s.auto_clears_in_window(r.t)}")
tripped, latched = r.soft_trip_and_clear()
check("fourth trip within 5 min LATCHES", tripped and latched and r.s.state == "TRIPPED",
      f"{r.s.state}/latched={r.s.trip_latched}")
r.hold(5.0 * MV_PER_MBAR, 10)
check("latched trip stays TRIPPED at 5 mbar(g)", r.s.state == "TRIPPED")
rep = r.step(5.0 * MV_PER_MBAR, "RESET")
check("RESET clears the latched trip -> IDLE", r.s.state == "IDLE", rep)
r.step(10.0 * MV_PER_MBAR, "START")
check("RESET starts a fresh auto-clear count", r.soft_trip_and_clear() == (True, False) and r.s.state == "RUN")

# 9. A30: the 5 min window expires.
r = Run(zero_ok=True)
r.run_at(10.0)
for _ in range(3):
    r.soft_trip_and_clear()
r.hold(10.0 * MV_PER_MBAR, C["SOFT_AUTOCLEAR_WINDOW_S"] + 1)
check("after 5 min the count is back to 0", r.s.auto_clears_in_window(r.t) == 0)
check("a new trip auto-clears again", r.soft_trip_and_clear() == (True, False) and r.s.state == "RUN")

# 10. A30: SENSOR_LOSS and HARD_LIMIT stay latched.
r = Run(zero_ok=True)
r.run_at(10.0)
r.hold(10.0 * MV_PER_MBAR, 1)
r.step(30.0 * MV_PER_MBAR)  # one-sample jump: HARD_LIMIT directly
check("HARD_LIMIT latched", r.s.reason == "HARD_LIMIT" and r.s.trip_latched)
r.to(10.0, rate=0.15)
r.hold(10.0 * MV_PER_MBAR, 2)
check("HARD_LIMIT does not auto-clear at 10", r.s.state == "TRIPPED")
r = Run(zero_ok=True)
r.run_at(10.0)
r.to(20.5)
r.to(29.5, rate=0.5)
check("SOFT trip escalates to latched HARD_LIMIT at 29", r.s.reason == "HARD_LIMIT" and r.s.trip_latched)
r = Run(zero_ok=True)
r.hold(120.0, 1)
r.step(120.0, "START")
r.hold(120.0, 31)
check("open-circuit SENSOR_LOSS latched", r.s.reason == "SENSOR_LOSS" and r.s.trip_latched)
r.hold(10.0 * MV_PER_MBAR, 2)
check("SENSOR_LOSS does not auto-clear on a valid 10 mbar(g)", r.s.state == "TRIPPED")

# 11. A30: a broken wire during a SOFT_LIMIT trip must not lead to an auto-clear.
r = Run(zero_ok=True)
r.run_at(10.0)
r.to(20.5)
r.hold(100.0, 5)  # wire breaks: 100 mV = 1.5 mbar(g), re-referenced after 1 s
check("drop during SOFT trip -> latched SENSOR_LOSS, no auto-clear",
      r.s.state == "TRIPPED" and r.s.reason == "SENSOR_LOSS" and r.s.trip_latched,
      f"{r.s.state}/{r.s.reason}")
r = Run(zero_ok=True)
r.run_at(10.0)
r.to(20.5)
for _ in range(6):
    r.t += DT
    r.s.step(r.t, 0.0, False, None, r.t)  # read failures -> stale after 0.5 s
r.to(10.0)
check("stale sensor during SOFT trip -> latched SENSOR_LOSS", r.s.state == "TRIPPED"
      and r.s.reason == "SENSOR_LOSS")

# 12. A30 (user decision): a SOFT_LIMIT trip that started in TEST latches.
r = Run(zero_ok=True)
r.hold(10.0 * MV_PER_MBAR, 1)
r.s.set_state("TEST", r.t)  # TEST commands are not simulated; enter the state directly
r.to(20.5)
check("SOFT_LIMIT trip from TEST is latched", r.s.state == "TRIPPED" and r.s.trip_latched)
r.to(10.0)
r.hold(10.0 * MV_PER_MBAR, 2)
check("TEST trip does not auto-clear (pump never restarts by itself)", r.s.state == "TRIPPED"
      and not r.s.pump_on)

# 13. A34: the team leak model is LOG AND DISPLAY ONLY. P_comp (and P_corr, leak_rate)
#     must never feed a trip, the warning, the PI or the plausibility checks.
import inspect  # noqa: E402
import re  # noqa: E402

FW = fr.ROOT / "firmware"
LEAK_TOKENS = re.compile(r"leak|p_comp|p_corr|PUMP_ON_OFFSET|K_LEAK|P_COMP_RESET", re.IGNORECASE)


def code_only(src: str) -> str:
    """C++ source without // and /* */ comments (string literals are not an issue here)."""
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.DOTALL)


# 13a. Static, firmware: the supervisor (every trip, warning, PI and plausibility decision)
#      has no reference to the leak model at all, not even in comments.
for name in ("supervisor.cpp", "supervisor.h"):
    hits = sorted({m.group(0) for m in LEAK_TOKENS.finditer((FW / "src" / name).read_text(encoding="utf-8"))})
    check(f"A34 {name} never references the leak model", not hits, ", ".join(hits))
includers = sorted(p.name for p in (FW / "src").glob("*.*") if re.search(
    r'#\s*include\s*"leak_model\.h"', p.read_text(encoding="utf-8")))
check("A34 leak_model.h included only by control_task.cpp and the log-only ring.cpp",
      includers == ["control_task.cpp", "ring.cpp"], str(includers))
ring_src = code_only((FW / "src" / "ring.cpp").read_text(encoding="utf-8"))
check("A34 ring.cpp uses only leak::rate() (rebuild of the logged leak_rate)",
      set(re.findall(r"leak::(\w+)", ring_src)) == {"rate"}, str(set(re.findall(r"leak::(\w+)", ring_src))))
ct = code_only((FW / "src" / "control_task.cpp").read_text(encoding="utf-8"))
i_step, i_leak = ct.find("sup.step("), ct.find("leak_model.update(")
check("A34 leak model computed after sup.step() in the control task", 0 <= i_step < i_leak,
      f"step@{i_step} leak@{i_leak}")
bad = [ln.strip() for ln in ct.splitlines() if "sup." in ln and re.search(r"\blr\b|leak|p_comp|p_corr", ln)]
check("A34 no leak model value is passed to the supervisor", not bad, "; ".join(bad))
uses = [ln.strip() for ln in ct.splitlines() if re.search(r"\blr\.", ln)]
ok_use = re.compile(r"s\.(p_corr|p_comp_assumed|leak_rate) = lr\.(p_corr|p_comp|leak_rate);")
check("A34 leak results only go into the logged Sample", len(uses) == 3 and all(ok_use.fullmatch(u) for u in uses),
      "; ".join(uses))

# 13b. Static, Python port: the simulated Supervisor never sees the leak model either.
hits = sorted({m.group(0) for m in LEAK_TOKENS.finditer(inspect.getsource(fr.Supervisor))})
check("A34 fake_rig Supervisor never references the leak model", not hits, ", ".join(hits))

# 13c. Model arithmetic (Python port of leak_model.h) at Ts = 0.1 s.
check("A34 per-step constants scaled to TS_S = 0.1 s", C["TS_S"] == 0.1 and C["CONTROL_PERIOD_MS"] == 100)
m = fr.LeakModel()
pc, pcomp, lr = m.update(4.0, True, 100.0)
exp_lr = C["K_LEAK_PUMP_ON"] * (4.0 + C["PUMP_ON_OFFSET"]) ** 0.5
check("A34 P_corr = P_f + 0.19 while the pump ran", abs(pc - 4.19) < 1e-9, f"{pc}")
check("A34 leak_rate = k_on * sqrt(P_corr)", abs(lr - exp_lr) < 1e-12, f"{lr}")
check("A34 one fully closed step adds leak_rate * 0.1 s (not 0.2 s)", abs(pcomp - (pc + exp_lr * 0.1)) < 1e-12,
      f"{pcomp - pc:.6f} mbar")
m = fr.LeakModel()
_, half, _ = m.update(4.0, True, 50.0)
check("A34 a 50 % closed step adds half (duty weighting)", abs(half - (4.19 + exp_lr * 0.05)) < 1e-12)
m = fr.LeakModel()
_, op, _ = m.update(4.0, False, 0.0)
check("A34 open valve adds nothing", op == 4.0)
m = fr.LeakModel()
m.update(4.0, True, 100.0)
m.update(4.0, True, 100.0)
_, kept, _ = m.update(0.04, True, 0.0)
check("A34 no reset while the pump is ON", kept > 0.04 + C["PUMP_ON_OFFSET"])
_, reset, _ = m.update(0.04, False, 0.0)
check("A34 reset when pump OFF and P_corr < 0.05", reset == 0.04 and m.lost == 0.0)
pc, pcomp, lr = fr.LeakModel().update(fr.NAN, False, 100.0)
check("A34 no P_f yet -> all NaN", all(x != x for x in (pc, pcomp, lr)))

# 13d. Closed loop: RUN at a measured 5 mbar(g), pump ON, vent closed by the PI for 5 min.
#      P_comp climbs past the 14 / 20 / 29 mbar(g) levels; nothing may react to it.
r = Run(zero_ok=True)
r.run_at(5.0)
m, mp, md = fr.LeakModel(), False, 0.0
max_comp, events = 0.0, set()
for _ in range(3000):
    r.step(5.0 * MV_PER_MBAR)
    _, pcomp, _ = m.update(r.s.p_f, mp, md)
    mp, md = r.s.pump_on, r.s.valve_duty
    max_comp = max(max_comp, pcomp)
    events |= {x for x, on in (("trip", r.s.state != "RUN"), ("warning", r.s.warning)) if on}
check("A34 P_comp rose past the hard limit in the scenario", max_comp > C["P_HARD_LIMIT"], f"{max_comp:.1f}")
check("A34 P_comp above 29 causes no trip and no warning (measured 5 mbar(g))", not events,
      f"{events} state={r.s.state}/{r.s.reason}")

# 13e. Dashboard (A35): P_comp_assumed is no longer shown at all; it stays in the CSV only.
static = fr.ROOT / "dashboard" / "static"
hits = sorted(p.name for p in static.glob("*.*") if re.search(r"P_comp|pcomp|comp", p.read_text(encoding="utf-8")))
check("A35 dashboard pages never reference P_comp_assumed", not hits, str(hits))
app_src = (fr.ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
check("A35 P_comp_assumed stays in the CSV columns", '"P_comp_assumed"' in app_src)

# ---------------------------------------------------------------------------------------
# 15. A35 SIM RAMP: the simulated pressure drives PI, warning and the 20/29 trips; the REAL
#     sensor protection (20/29 on the real pressure, stale, drop, open circuit) stays on.
# ---------------------------------------------------------------------------------------
import analyze_log as al  # noqa: E402


def sim_run(rate=20, real=3.0, zero=0.0, zero_ok=True, start="IDLE"):
    r = Run(zero_ok=zero_ok, zero=zero)
    r.hold(real * MV_PER_MBAR, 1)
    if start == "TEST":
        r.s.set_state("TEST", r.t)
    return r, r.step(real * MV_PER_MBAR, f"SIM RAMP {rate}")


r, rep = sim_run(zero_ok=False)
check("A35 SIM RAMP refused without a saved zero", r.s.state == "IDLE" and "ZERO" in rep, rep)
r = Run(zero_ok=True)
r.run_at(10.0)
rep = r.step(10.0 * MV_PER_MBAR, "SIM RAMP 20")
check("A35 SIM RAMP refused from RUN", r.s.state == "RUN" and rep.startswith("ERR"), rep)
r, rep = sim_run(rate=15)
check("A35 SIM RAMP rate other than 10/20/30 refused", r.s.state == "IDLE" and "10, 20 or 30" in rep, rep)
r, rep = sim_run(start="TEST")
check("A35 SIM RAMP accepted from TEST", r.s.state == "SIM", rep)
r, rep = sim_run(rate=20, real=3.0)
check("A35 SIM RAMP accepted from IDLE, starts at the real P_f", r.s.state == "SIM" and abs(r.s.sim_p0 - 3.0) < 1e-6,
      rep)
r.step(3.0 * MV_PER_MBAR)
check("A35 in SIM: pump ON, PCV-101 energized (closed) at 100 %",
      r.s.pump_on and r.s.valve_duty == 100.0 and r.s.sim_active, f"duty={r.s.valve_duty}")
p_a, t_a = r.s.p_raw, r.t
r.hold(3.0 * MV_PER_MBAR, 10)
rate_meas = (r.s.p_raw - p_a) / ((r.t - t_a) / 1e6) * 60.0
check("A35 simulated pressure ramps at the commanded rate", abs(rate_meas - 20.0) < 0.01, f"{rate_meas:.4f} mbar/min")
check("A35 real pressure is kept separately (P_real)", abs(r.s.p_real - 3.0) < 1e-6 and r.s.p_raw > 5.0,
      f"P_sim={r.s.p_raw:.2f} P_real={r.s.p_real:.2f}")
r.hold(3.0 * MV_PER_MBAR, 15)  # sim ~ 11.3: above the setpoint
check("A35 above the setpoint the PI is computed (u_open > 0) but the vent stays closed (duty 100 %)",
      r.s.u_open > 0.0 and r.s.valve_duty == 100.0 and r.s.pump_on,
      f"P_sim={r.s.p_raw:.2f} u_open={r.s.u_open:.1f}")

# Full ramps: warning before 20, SOFT_LIMIT trip on the simulated pressure, latched.
# Real input = the rig's measured pump-ON reading (314 mV with the 2.13 mbar zero, A25):
# a 0 V real reading with the pump ON would (correctly) trip the open-circuit check.
RIG_MV, RIG_ZERO = 314.0, 2.13
leads = {}
for rate in (10, 20, 30):
    r, _ = sim_run(rate=rate, real=RIG_MV * 0.015 - RIG_ZERO, zero=RIG_ZERO)
    rows, t_warn, trip_row = [], None, None
    for _ in range(int(25 / rate * 60 * 10) + 50):
        r.step(RIG_MV)
        if r.s.warning and t_warn is None:
            t_warn = r.t
        if r.s.state == "TRIPPED":
            trip_row = (r.s.sim_active, r.s.p_raw, r.s.reason, r.s.trip_latched, r.s.pump_on, r.s.valve_duty)
            break
        if not r.s.warning:
            t_warn = None
    leads[rate] = None if t_warn is None else (r.t - t_warn) / 1e6
    check(f"A35 SIM {rate}: SOFT_LIMIT trip at simulated 20 mbar(g), latched, pump OFF, vent open",
          trip_row is not None and trip_row[0] and trip_row[1] >= 20.0 and trip_row[2] == "SOFT_LIMIT"
          and trip_row[3] and not trip_row[4] and trip_row[5] == 0.0, str(trip_row))
    r.step(RIG_MV)
    check(f"A35 SIM {rate}: after the trip the pressure is the real one again",
          not r.s.sim_active and r.s.p_raw == r.s.p_real and r.s.state == "TRIPPED")
    rep = r.step(RIG_MV, "RESET")
    check(f"A35 SIM {rate}: RESET uses the real pressure (2.6 < 20) -> IDLE", r.s.state == "IDLE", rep)
print(f"  info: SIM warning leads before the trip: "
      + ", ".join(f"{k} mbar/min {v:.1f} s" if v is not None else f"{k}: none" for k, v in leads.items()))

# Rig case (SIM RAMP rig result 2026-11-16): the REAL reading follows the valve. Pump ON with
# the vent closed holds the rig plateau (~350 mV, zero 2.13 mbar); an open vent vents the
# real pressure toward 0 (142 mV, the ADC floor). The earlier PI-drives-valve version opened
# the vent above 10 mbar(g) simulated and tripped SENSOR_LOSS at P_sim 15.05 on the rig.
# Every rate must now end with a SOFT_LIMIT trip at the simulated 20 mbar(g).
PLATEAU_MV, VENTED_MV, TAU_S = 350.0, 142.0, 5.0
for rate in (10, 20, 30):
    for start_mv in (PLATEAU_MV, VENTED_MV):  # already at the plateau, or from a vented vessel
        r = Run(zero_ok=True, zero=RIG_ZERO)
        r.hold(start_mv, 1)
        mv = start_mv
        r.step(mv, f"SIM RAMP {rate}")
        min_mv, end = float("inf"), None
        for _ in range(int((20.0 - (start_mv * 0.015 - RIG_ZERO)) / rate * 600) + 100):
            closed = r.s.pump_on and r.s.valve_duty / 100.0
            target = VENTED_MV + (PLATEAU_MV - VENTED_MV) * (closed or 0.0)
            mv += (target - mv) * (0.1 / TAU_S)
            r.step(mv)
            if r.s.sim_active and r.s.state == "SIM":
                min_mv = min(min_mv, mv) if r.s.p_raw > 10.0 else min_mv
            if r.s.state == "TRIPPED":
                end = (r.s.reason, r.s.p_raw, r.s.sim_active, r.s.trip_latched, r.s.pump_on, r.s.valve_duty)
                break
        check(f"A35 rig plateau {rate} mbar/min from {start_mv:.0f} mV: SOFT_LIMIT at simulated 20, not SENSOR_LOSS",
              end is not None and end[0] == "SOFT_LIMIT" and end[1] >= 20.0 and end[2] and end[3],
              f"{end} min real V_gpio above sim 10: {min_mv:.0f} mV")
        check(f"A35 rig plateau {rate} mbar/min from {start_mv:.0f} mV: trip outputs pump OFF, valve released",
              end is not None and not end[4] and end[5] == 0.0, str(end))

# Real background protection while the simulated pressure is low.
r, _ = sim_run(rate=10, real=3.0)
r.hold(3.0 * MV_PER_MBAR, 2)
r.to(20.5, rate=1.5)  # REAL pressure rises to 20.5 (sim still ~3.5)
check("A35 REAL pressure >= 20 trips SOFT_LIMIT in SIM (latched)",
      r.s.state == "TRIPPED" and r.s.reason == "SOFT_LIMIT" and r.s.trip_latched and r.s.p_real >= 20.0,
      f"{r.s.state}/{r.s.reason} P_real={r.s.p_real:.2f}")
r, _ = sim_run(rate=10, real=3.0)
r.hold(3.0 * MV_PER_MBAR, 2)
r.step(29.5 * MV_PER_MBAR)  # one-sample jump of the REAL pressure
check("A35 REAL pressure >= 29 trips HARD_LIMIT in SIM", r.s.state == "TRIPPED" and r.s.reason == "HARD_LIMIT",
      f"{r.s.state}/{r.s.reason}")
r, _ = sim_run(rate=10, real=0.0, zero=2.13)
r.hold(147.7, 31)  # real open circuit (A25 max) while the simulated pressure ramps
check("A35 REAL open circuit trips SENSOR_LOSS in SIM after 30 s",
      r.s.state == "TRIPPED" and r.s.reason == "SENSOR_LOSS", f"{r.s.state}/{r.s.reason} P_sim={r.s.p_raw:.2f}")
r, _ = sim_run(rate=10, real=8.0)
r.hold(8.0 * MV_PER_MBAR, 1)
r.hold(0.0, 1)  # REAL wire break: 8 -> 0 mbar(g) drop is rejected, sensor goes stale
check("A35 REAL wire break (drop + stale) trips SENSOR_LOSS in SIM", r.s.state == "TRIPPED"
      and r.s.reason == "SENSOR_LOSS", f"{r.s.state}/{r.s.reason}")
r, _ = sim_run(rate=10, real=3.0)
for _ in range(6):
    r.t += DT
    r.s.step(r.t, 0.0, False, None, r.t)  # REAL read failures
check("A35 REAL read failures (stale) trip SENSOR_LOSS in SIM", r.s.state == "TRIPPED"
      and r.s.reason == "SENSOR_LOSS")

# Static, firmware: the C++ trip evaluation keeps the REAL-pressure terms in every state
# (this file otherwise tests the Python port).
sup_src = code_only((FW / "src" / "supervisor.cpp").read_text(encoding="utf-8"))
ev = sup_src[sup_src.find("void Supervisor::evaluateTrip"):sup_src.find("Outputs Supervisor::computeOutputs")]
check("A35 firmware soft trip includes the REAL pressure term",
      "(valid_ && p_real_ >= P_SOFT_LIMIT)" in ev)
check("A35 firmware hard trip includes the REAL pressure term",
      "(valid_ && p_real_ >= P_HARD_LIMIT)" in ev)
check("A35 firmware RESET checks the REAL pressure", "!(p_real_ < RESET_MAX_PRESSURE)" in sup_src)
check("A35 firmware sensor checks never use the simulated pressure",
      "sim" not in sup_src[sup_src.find("void Supervisor::validate"):sup_src.find("void Supervisor::updateControlPressure")])

# Ending SIM.
r, _ = sim_run(rate=20, real=3.0)
r.hold(3.0 * MV_PER_MBAR, 3)
rep = r.step(3.0 * MV_PER_MBAR, "SIM EXIT")
check("A35 SIM EXIT -> IDLE, pump OFF, valve released", r.s.state == "IDLE" and not r.s.pump_on
      and r.s.valve_duty == 0.0, rep)
r.step(3.0 * MV_PER_MBAR)
check("A35 after SIM EXIT the control pressure is the real one", r.s.p_raw == r.s.p_real and not r.s.sim_active)
r, _ = sim_run(rate=20, real=3.0)
r.hold(3.0 * MV_PER_MBAR, 3)
rep = r.step(3.0 * MV_PER_MBAR, "STOP")
check("A35 STOP also ends SIM", r.s.state == "IDLE" and not r.s.pump_on, rep)

# Outside SIM nothing changed: the control pressure is the real pressure in every sample.
r = Run(zero_ok=True)
r.run_at(10.0)
same = True
for _ in range(100):
    r.step(10.0 * MV_PER_MBAR)
    same = same and r.s.p_raw == r.s.p_real and not r.s.sim_active
check("A35 outside SIM P_raw == P_real (no change to RUN)", same)

# analyze_log.py SIM report on a synthetic log from the Python port.
rows = []
r, _ = sim_run(rate=30, real=RIG_MV * 0.015 - RIG_ZERO, zero=RIG_ZERO)
seq = 0
for _ in range(700):
    r.step(RIG_MV)
    tripped_now = r.s.trip_new
    if tripped_now:
        r.s.trip_output_us = r.s.trip_decision_us + 40
    rows.append({"seq": str(seq), "t_esp_ms": str(r.t / 1000.0), "P_raw": str(r.s.p_raw),
                 "P_real": str(r.s.p_real), "simulated": "1" if r.s.sim_active else "0",
                 "sim_rate": str(r.s.sim_rate if r.s.sim_active else 0), "state": r.s.state,
                 "warning": str(r.s.warning), "trip_reason": r.s.reason,
                 "trip_decision_us": str(r.s.trip_decision_us), "trip_output_us": str(r.s.trip_output_us)})
    seq += 1
    if r.s.state == "TRIPPED" and not r.s.sim_active:
        break
res = al.sim_runs(rows, quiet=True)
check("A35 analyze_log finds one SIM run with a trip", len(res) == 1 and res[0]["trip"], str(res))
if res:
    check("A35 analyze_log fitted slope within +/-10 % of 30 mbar/min", res[0]["slope_ok"], f"{res[0]['fit']:.3f}")
    check("A35 analyze_log reports the warning lead and decision->output time",
          res[0]["lead_s"] is not None and res[0]["dec_out_us"] == 40 and res[0]["trip_reason"] == "SOFT_LIMIT",
          str(res[0]))
    check("A35 analyze_log warning lead matches the direct measurement (+/-0.1 s)",
          leads[30] is not None and abs(res[0]["lead_s"] - leads[30]) <= 0.1001,
          f"{res[0]['lead_s']} vs {leads[30]}")

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
