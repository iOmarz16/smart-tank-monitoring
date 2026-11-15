"""Regression checks of the pre-flash review rules (C1, M3) on the fake_rig Python port
of supervisor.cpp.

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
MV_PER_MBAR = 1000.0 / (C["DIVIDER_RATIO"] * C["SENSOR_MBAR_PER_V"])
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
check("10 mbar/min ramp passes 250 mV before 30 s", r.s.state == "RUN", f"{r.s.state}/{r.s.reason}")

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

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
