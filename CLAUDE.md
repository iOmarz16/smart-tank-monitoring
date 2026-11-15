# TEAM M021 — ICS firmware + local dashboard

Pressure control of the air headspace (5.0 L) in a sealed 10 L vessel (VEVOR JY-SS10L).
All pressures are gauge, in **mbar(g)**. This is NOT a water-level project.

## Source of truth
- `docs/TEAM_M021_Third_Report.pdf`. Precedence when the report contradicts itself:
  **Chapter 5 (as built) + Table B (BOM) + Figure 12 (wiring) > Chapter 4 (older design).**
- Chapter 4's ABP2 I2C sensor, MG996R servo, Crydom SSR and hardware E-stop do NOT exist. Ignore them.
- Every assumption goes in `docs/assumptions.md` with the report section it relates to.
- If the report is ambiguous or conflicts with these rules: STOP and ask. Never guess silently.

## Hard rules
- Do NOT change pins, thresholds, gains or timings without asking the user.
- No Internet, cloud, NTP, CDN or external hostnames in firmware or dashboard (Constraint 4, test I4).
- Do not install global packages without asking. Do not flash any board.
- Code and comments in English. Commit after each completed step.
- The dashboard is view-and-log only: no control of pump, valve or trip. It can never reset a trip.

## Hardware (Figure 12)
| Item | Detail |
|---|---|
| Controller | ESP32 30-pin DevKit, CH340 USB |
| PT-101 | Siemens QBM2030-30, 0–10 V = 0–30 mbar(g) (range jumper on 3000 Pa), 24 V supply |
| Sensor input | 33 kΩ / 10 kΩ divider + 100 nF → **GPIO34** (ADC1). Max 2.33 V at pin |
| Scaling | `V_sensor = V_gpio × 4.3`; `P_raw = V_sensor × 3.0 − zero_offset` |
| PCV-101 | 12 V solenoid, **normally open**, MOSFET 1, TRIG **GPIO26**. HIGH = energized = CLOSED |
| P-101 | 5 V pump, MOSFET 2, TRIG **GPIO27**. HIGH = pump ON |
| Fail-safe | 10 kΩ pull-downs on both TRIG lines → reset/power loss = pump OFF, valve OPEN |
| E-stop | none (ON-OFF switch only) |

## Control facts
- Loop 100 ms (FreeRTOS, core 1, vTaskDelayUntil); networking on core 0, never blocks control.
- Setpoint 10, warning 14, soft limit 20, hard trip 29 mbar(g). Sensor timeout 0.5 s.
- PI: Kc = 36 %/mbar, Ti = 8 s, Ts = 0.1 s; `u_open` = vent opening %, closed duty = 100 − u_open.
- Trip is evaluated every loop BEFORE PI, in every state, and latches. Reset only via serial `RESET`.
- Valve PWM ≤ 10 Hz (LEDC). On trip, outputs go LOW in the same loop (not at the next PWM edge).

## Layout
- `firmware/` PlatformIO project (all constants in `include/config.h`)
- `dashboard/` Python 3.13 view/log app, local assets only
- `tools/fake_rig.py` protocol-identical simulator; `tools/analyze_log.py` I1/I2/I5 analysis
- `docs/` report, assumptions, protocol

## Tests (Table I)
I1 latency ≤ 1 s · I2 loop period / no gap after 60 s Wi-Fi outage · I3 trip timing ·
I4 no Internet · I5 PI recomputation ≤ 1 % · I6 warning lead ≥ 10 s, vent ≤ 1 s after true 20 mbar(g).
