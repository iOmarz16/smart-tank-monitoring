"""DEMO mode scenarios for tools/fake_rig.py (supervisor meeting, no ESP32).

One scenario per specification. Each scenario is a script, ticked once per 100 ms
control loop BEFORE the plant step, that does only what an operator or the test
bench would do on the real rig:
  - operator commands go through the normal command queue (START, TEST, TEST PUMP ON ...),
    exactly as typed on the USB serial port, and are executed by the ported Supervisor;
  - test-bench actions change the simulated plant (inflow, a blocked vent, a broken
    PT-101 wire, a gas slug) or the simulated Wi-Fi link.
The Supervisor logic, its limits and its trips are never touched by this file.

Every scenario starts from a simulated controller restart (fresh Supervisor, the NVS
zero kept), so each run begins from a known state. seq keeps counting, so one dashboard
session holds every demo run.

Demo speed (1x / 2x / 4x) scales the PLANT's process time in S1 only: S1 judges the
ramp slope against process time, so a faster plant changes nothing in the result.
All other scenarios measure real-time behaviour (latency, loop period, trip timing,
warning lead, recovery time), so they always run at 1x.

This is a test tool. It is NOT a verified model of the rig.
"""

from __future__ import annotations

import random
from typing import Callable, Iterator

# (id, dropdown label), in the order of the Specifications panel. S1 first (user request).
SCENARIOS = [
    ("S1", "S1 · 10/20/30 mbar/min ramps"),
    ("S4", "S4 · UI latency"),
    ("S5", "S5 · loop and logging (20 s Wi-Fi outage)"),
    ("S6a", "S6a · 20 mbar(g) trip"),
    ("S6b", "S6b · sensor loss"),
    ("IS1", "IS1 · early warning"),
    ("IS2", "IS2 · disturbance recovery"),
    ("IS3", "IS3 · safety response"),
    ("C4", "C4 · offline operation"),
]
SCENARIO_IDS = [s for s, _ in SCENARIOS]
SPEEDS = (1, 2, 4)

# S1: vent closed, inflows of Table F test T1 (HYSYS R01-R03). With the plant's
# C = 5.20 std cm3/mbar they give 9.48 / 18.97 / 28.45 mbar/min, as in the report.
S1_RAMPS = ((10, 49.3), (20, 98.7), (30, 148.0))
S1_STOP_P = 15.0         # mbar(g): each ramp stops here (below the 20 trip)
S1_VENTED_P = 0.2       # mbar(g): "vented to 0" = P_f at or below this for 1 s
S1_FLOW_SIGMA = 0.01     # 1 % pump-flow scatter per ramp (small realistic noise)
# IS1: inflow = rate x C, so the ramp from the 10 mbar(g) hold runs at the nominal rate.
IS1_RAMPS = ((10, 52.0), (20, 104.0), (30, 156.0))
NOMINAL_INFLOW = 98.7    # std cm3/min (P6 baseline)
TRIP_RAMP_INFLOW = 148.0  # std cm3/min with the vent blocked (P5 / I3a)
IS2_SLUG_MBAR = 4.0      # R07-style 4 mbar disturbance ...
IS2_STEP_INFLOW = 148.0  # ... plus the P6 inflow step 98.7 -> 148 std cm3/min
WIFI_OUTAGE_S = 20.0


class Director:
    def __init__(self, rig) -> None:
        self.rig = rig
        self.sid = ""          # running scenario ("" = none)
        self.phase = "idle"
        self.mark = ""         # event on this loop's sample only
        self.speed = 1
        self.t_proc = 0.0      # process (plant) time since the scenario started, s
        self.pending: str | None = None
        self.gen: Iterator[None] | None = None

    # --- control-channel requests (applied at the next tick) -------------------
    def request(self, sid: str) -> bool:
        if sid not in SCENARIO_IDS:
            return False
        self.pending = sid
        return True

    def set_speed(self, x: int) -> bool:
        if x not in SPEEDS:
            return False
        self.speed = x
        return True

    def scale(self) -> int:
        return self.speed if self.sid == "S1" else 1

    def state(self) -> dict:
        return {"type": "demo_state", "scenario": self.sid, "phase": self.phase,
                "speed": self.speed, "time_scale": self.scale(), "t_proc": round(self.t_proc, 2),
                "wifi_down": self.rig.wifi_down, "seq": self.rig.seq}

    def fields(self) -> dict:
        return {"demo_scenario": self.sid, "demo_phase": self.phase,
                "demo_t_s": round(self.t_proc, 3), "demo_speed": self.scale(), "demo_mark": self.mark}

    # --- per loop ------------------------------------------------------------
    def tick(self, dt: float) -> float:
        """Advance the script by one loop. Returns the plant time step to use."""
        self.mark = ""
        if self.pending:
            sid, self.pending = self.pending, None
            self._start(sid)
        step = dt * self.scale()
        self.t_proc += step
        if self.gen is not None:
            try:
                next(self.gen)
            except StopIteration:
                self.gen = None
        return step

    def _start(self, sid: str) -> None:
        p0 = 0.0 if sid == "S1" else 10.0  # S1 starts vented; the others from the 10 hold
        self.rig.reboot(p0)
        self.sid, self.t_proc = sid, 0.0
        self.phase = f"{sid} start"
        self.mark = f"{sid} start"
        self.gen = getattr(self, "_" + sid)()
        print(f"[demo] scenario {sid} started", flush=True)

    # --- helpers -------------------------------------------------------------
    @property
    def sup(self):
        return self.rig.sup

    @property
    def plant(self):
        return self.rig.plant

    def cmd(self, c: str) -> None:
        self.rig.cmd_queue.append(c)

    def _wait(self, s: float) -> Iterator[None]:
        end = self.t_proc + s
        while self.t_proc < end - 1e-9:
            yield

    def _until(self, cond: Callable[[], bool], hold_s: float = 0.0) -> Iterator[None]:
        since = None
        while True:
            if cond():
                since = self.t_proc if since is None else since
                if self.t_proc - since >= hold_s - 1e-9:
                    return
            else:
                since = None
            yield

    def _forever(self) -> Iterator[None]:
        while True:
            yield

    def _start_run(self, phase: str) -> Iterator[None]:
        yield from self._wait(0.3)      # BOOT -> IDLE on the first valid sample
        self.cmd("START")
        self.phase = phase
        yield from self._until(lambda: self.sup.state == "RUN")

    # --- scenarios -----------------------------------------------------------
    def _S1(self) -> Iterator[None]:
        # Spec 1: three ramps back to back in TEST mode, pump ON and vent closed, each from 0.
        yield from self._wait(0.3)
        self.cmd("TEST")
        yield from self._until(lambda: self.sup.state == "TEST")
        for rate, q in S1_RAMPS:
            self.plant.inflow = q * random.gauss(1.0, S1_FLOW_SIGMA)
            self.phase = f"S1 ramp {rate}"
            self.mark = f"S1 ramp {rate} start"
            self.cmd("TEST VALVE ENERGIZE")
            self.cmd("TEST PUMP ON 300")
            yield from self._until(lambda: self.sup.valid and self.sup.p_raw >= S1_STOP_P)
            self.phase = f"S1 vent {rate}"
            self.mark = f"S1 ramp {rate} stop"
            self.cmd("TEST PUMP OFF")
            self.cmd("TEST VALVE RELEASE")
            yield from self._until(lambda: self.sup.p_f <= S1_VENTED_P, hold_s=1.0)
        self.cmd("TEST EXIT")
        self.phase = "S1 done"
        self.mark = "S1 done"
        yield from self._forever()

    def _hold(self, sid: str) -> Iterator[None]:
        yield from self._start_run(f"{sid} hold at 10")
        yield from self._forever()

    def _S4(self) -> Iterator[None]:
        yield from self._hold("S4")

    def _C4(self) -> Iterator[None]:
        yield from self._hold("C4")

    def _S5(self) -> Iterator[None]:
        yield from self._start_run("S5 hold at 10")
        yield from self._wait(10.0)
        self.phase = "S5 Wi-Fi down"
        self.mark = "wifi down"
        self.rig.set_wifi(False)
        yield from self._wait(WIFI_OUTAGE_S)
        self.rig.set_wifi(True)
        self.phase = "S5 Wi-Fi restored"
        self.mark = "wifi up"
        yield from self._forever()

    def _trip_ramp(self, sid: str) -> Iterator[None]:
        # P5 / I3a: vent blocked, 148 std cm3/min, until the 20 mbar(g) trip.
        yield from self._start_run(f"{sid} hold at 10")
        yield from self._wait(5.0)
        self.phase = f"{sid} ramp to 20"
        self.mark = f"{sid} ramp start"
        self.plant.inflow = TRIP_RAMP_INFLOW
        self.plant.vent_blocked = True
        yield from self._until(lambda: self.sup.state == "TRIPPED")
        self.plant.vent_blocked = False   # the block was the test fault; the vent now acts
        self.plant.inflow = NOMINAL_INFLOW
        self.phase = f"{sid} tripped"
        yield from self._until(lambda: self.sup.state == "RUN")  # A30 auto-clear at 10
        self.phase = f"{sid} auto-cleared, back in RUN"
        yield from self._forever()

    def _S6a(self) -> Iterator[None]:
        yield from self._trip_ramp("S6a")

    def _IS3(self) -> Iterator[None]:
        yield from self._trip_ramp("IS3")

    def _S6b(self) -> Iterator[None]:
        yield from self._start_run("S6b hold at 10")
        yield from self._wait(8.0)
        self.phase = "S6b signal lost"
        self.mark = "signal lost"
        self.plant.wire_broken = True
        yield from self._until(lambda: self.sup.state == "TRIPPED")
        self.phase = "S6b tripped"
        yield from self._wait(5.0)
        self.plant.wire_broken = False
        self.phase = "S6b signal restored; trip latched (RESET at the rig)"
        self.mark = "signal restored"
        yield from self._forever()

    def _IS1(self) -> Iterator[None]:
        yield from self._start_run("IS1 hold at 10")
        yield from self._wait(5.0)
        for rate, q in IS1_RAMPS:
            self.phase = f"IS1 ramp {rate}"
            self.mark = f"IS1 ramp {rate} start"
            self.plant.inflow = q
            self.plant.vent_blocked = True
            yield from self._until(lambda: self.sup.state == "TRIPPED")
            self.plant.vent_blocked = False
            self.plant.inflow = NOMINAL_INFLOW
            self.phase = f"IS1 recover {rate}"
            yield from self._until(lambda: self.sup.state == "RUN")
            yield from self._wait(3.0)
        self.phase = "IS1 done"
        self.mark = "IS1 done"
        yield from self._forever()

    def _IS2(self) -> Iterator[None]:
        yield from self._start_run("IS2 hold at 10")
        yield from self._wait(20.0)       # let the PI settle at 10
        self.phase = "IS2 after disturbance"
        self.mark = "disturbance"
        self.plant.p += IS2_SLUG_MBAR
        self.plant.inflow = IS2_STEP_INFLOW
        yield from self._forever()
