# Assumptions, contradictions and open questions

Source: `TEAM_M021_Third_Report.pdf` (127 pages). Precedence: Ch. 5 + Table B + Fig. 12 > Ch. 4.
Status key: **OPEN** = waiting for the user · **ASSUMED** = decided, reversible · **CONFIRMED** = user approved.

## A. Contradictions found in the report

| # | Topic | Where | What conflicts | Handling |
|---|---|---|---|---|
| C1 | Pump off at 20 vs 29 | Spec 6 (p. 24) vs Table D (§5.1.2) | Spec 6: "injection stops and the vent opens" at 20. Table D layer 3 (20): vent 100 % open only; layer 4 (29): pump OFF. | `PUMP_OFF_AT_SOFT_LIMIT = true` (user instruction). Commented in code. Awaiting CHE. |
| C2 | Soft limit latches vs auto re-close | Fig. 7 + §4.3.1.2 vs Table F test T3 (§5.2.1) | Fig. 7/§4.3.1.2: trip latches, reset only by operator. T3 (HYSYS R04): "open 100 % at ≥ 20, **re-close at ≤ 10**" (re-closed at 48.28 s), i.e. auto-recovery with hysteresis. Rig test P5 says "as T3". Latch kept (user, Q1). P5 uses serial `RESET`; T3's auto re-close is not reproduced on the rig. CONFIRMED |
| C3 | E-stop | Eq. 8, Fig. 7 (E-stop state, rule 1) vs Table C | Eq. 8 includes `emergency_stop`; Table C: no E-stop, ON-OFF switch only. | Dropped from firmware (Table C wins). |
| C4 | Sensor and validity rule | §4.3.1.2 Eq. 9 vs Table C, §5.2.2 | Eq. 9 is for the I2C ABP2 (0–60). As built: analog QBM2030, broken wire reads 0 V → needs the 2 mbar drop check (§5.2.2). | User rule: range [-2, 31] + drop check. See Q4, Q5. |
| C5 | Vent actuator | §4.3.1.2 Eq. 13 (servo θ = u_open) vs Table C | Servo position command replaced by NO solenoid with time-proportioned PWM ≤ 10 Hz. | PWM; closed duty = 100 − u_open. |
| C6 | State machine | Fig. 7 vs user §2.6 | Fig. 7 states: INITIALIZATION → NORMAL CONTROL (auto, on "self-test OK + valid sample"), EARLY WARNING (state), SENSOR FAULT ("hold last safe command", returns to normal if valid < 0.5 s), PRESSURE TRIP, MANUAL E-STOP. No IDLE / START. User: BOOT → IDLE → RUN → TRIPPED. BOOT → IDLE → RUN → TRIPPED (+ TEST for the serial test mode); warning and sensor_fault are flags (Q3). CONFIRMED |
| C7 | Warning confirmation | §4.2.2 (Block 9 text, p. 50) vs §4.3.1.2 | Block 9 text: warn "only after **several consecutive** valid, sufficiently steep, positive-slope estimates". §4.3.1.2 rule has no confirmation count and no slope threshold value. 0.05 mbar/s, 3 consecutive samples for the slope rule; 14 mbar(g) rule immediate (Q2). CONFIRMED |
| C8 | PID vs PI | §4.3.1.2 Eq. 12 (Kp, Ki, Kd unassigned) vs §5.1 / Table 5 | As built: PI from HYSYS, Kc = 36, Ti = 8 s, Kd = 0. | PI, `u = Kc(e + Ts/Ti·Σe)`. |
| C9 | Power-loss behaviour of vent | §4.3.1.2 (servo not driven open on power loss) vs Table C/D | As built: NO valve vents on power loss. | Ch. 5 wins. |

## B. Assumptions

| # | Assumption | Report reference | Status |
|---|---|---|---|
| A1 | Trip reset condition = `P_raw < 20.0` mbar(g) AND sensor valid AND not stale AND serial `RESET`. So `RESET_MAX_PRESSURE = 20.0` (strict `<`). | §4.3.1.2 ("Reset requires pressure below 20 mbar(g), a valid non-stale sample, and deliberate local operator action"); Fig. 7 rule 7 | CONFIRMED (report rule; latch kept) |
| A2 | `P_f` = first-order EMA, `α = 0.5` (lowest value the report allows). | §4.3.1.2 Eq. 11 ("α ≥ 0.5 … final α from bench data, Task C3") | ASSUMED |
| A3 | On trip, the valve LEDC channel is detached and GPIO26 is driven LOW directly, because an LEDC duty change only takes effect at the next PWM period (up to 100 ms later). | §5.2.2 Spec 6 / I3 | ASSUMED (implementation detail) |
| A4 | During a sensor fault shorter than 0.5 s (invalid sample, not yet stale), the PI integrator is frozen and the last valve duty is held. | Fig. 7 SENSOR FAULT "hold last safe command" | CONFIRMED (Q3) |
| A5 | First valid sample after boot (or after `ZERO`) has no "last valid" reference, so the drop check is skipped for it. | §5.2.2 | ASSUMED |
| A6 | Zero offset is in mbar, stored in NVS, default 0.0 if never set. `ZERO` is accepted only in IDLE with pump OFF and valve OPEN (vessel vented). | §5.1.1 step 8 | ASSUMED |
| A7 | QBM2030-30 range selector must be set to 3000 Pa (BOM lists 1000/1500/3000 Pa). README will tell the operator to check it. | Table B item 7 | ASSUMED |
| A8 | The 4 relay modules in Table B (item 13) are not used; Fig. 12 uses MOSFETs only. | Table B vs Fig. 12 | ASSUMED |
| A9 | `t_to_limit` reported as null/absent when slope ≤ `MIN_WARNING_SLOPE`. | §4.3.1.2 ("for a positive accepted slope") | ASSUMED |
| A10 | The ML early-warning model (§5.2.2, Table H) is out of scope for this step; firmware warning is the rule-based one. The dashboard leaves room to add it later. | §5.2.2 "Limitations and safety" | ASSUMED |
| A11 | Lag compensation, when enabled, uses the 5-sample regression slope of P_f; it applies to the soft-limit trip only, not the hard trip. | §5.2.2 (I6b fallback, P + 1 s·dP/dt) | ASSUMED |
| A12 | Valve PWM: 10 Hz, 14-bit LEDC (10-bit cannot reach 10 Hz from the 80 MHz APB clock; 14-bit can). | Table C (PWM ≤ 10 Hz) | ASSUMED |
| A13 | `ZERO` is refused if the measured offset exceeds ±3.0 mbar (10 % FS, `ZERO_MAX_ABS_OFFSET`): the vessel is probably not vented, or the range jumper is wrong. New constant, not from the report. | §5.1.1 step 8 | ASSUMED — please confirm |
| A14 | `TEST DUTY <n>` sets the **energized (closed)** duty of PCV-101, the same quantity as `valve_duty`. `TEST VALVE ENERGIZE` = 100 %, `RELEASE` = 0 %. `TEST` (no argument) enters test mode, only from IDLE. | brief §2.7 | ASSUMED |
| A15 | Trip check also runs in BOOT and IDLE. No valid sample within 0.5 s of boot → TRIPPED / SENSOR_LOSS. | brief §2.3/§2.6, Fig. 7 | ASSUMED |
| A16 | With `PUMP_OFF_AT_SOFT_LIMIT = false`, a soft trip keeps the pump in its previous state (ON only if it was ON). Any later hard limit or sensor loss turns it OFF and escalates `trip_reason`. | Table D layers 3–4 | ASSUMED (flag is `true` today) |
| A17 | Replay: on every WebSocket (re)connect the whole ring buffer (1500 samples = 150 s) is re-sent with `replayed = true`; live samples sent ≥ 3 samples behind the newest are also flagged `replayed`. The dashboard deduplicates by `seq` (first copy wins). The ESP32 cannot know what the client already has, because the only inbound message is ping. | brief §3, I2 | ASSUMED |
| A18 | Trip timestamps: `trip_decision_us` = esp_timer when the trip is decided; `trip_output_us` = esp_timer right after `valveSafe()` + pump LOW. These are software timestamps; Table I's t3 uses a spare ESP32 input reading the MOSFET outputs, which needs a wire and a pin (not implemented: no pin assigned). | §5.2.2 Fig. 16, I3 | OPEN — pin for t3 read-back? |
| A19 | Plausibility check uses `P_raw` of valid samples; its 30 s timer starts at the first valid sample below 1.0 mbar(g) in RUN with the pump ON and clears when a valid sample ≥ 1.0 is seen. Invalid samples neither start nor clear it. | user answer Q5 | ASSUMED |
| A20 | Task watchdog timeout is 1 s (IDF 4.4 accepts whole seconds only). A stall shorter than 1 s is not caught by the watchdog. Core-0 idle task is also monitored (core default). | brief §2.1, Spec 6 | ASSUMED |

## C. Sensor-loss blind spot and its mitigation

The drop check (§5.2.2) detects a broken PT-101 wire only when the pressure at that moment is above
2.0 mbar(g): the reading falls to 0 V = 0 mbar(g), a drop > 2 mbar per sample. Below 2 mbar(g) the
drop is too small, and 0 mbar(g) is inside the valid range, so the fault is **not detectable** by the
validity rule. The worst case is RUN start-up (vessel near 0 mbar(g)) with a broken wire: the pump
runs, the PI sees e = −10 and keeps the vent closed, and pressure rises unseen.

Mitigation (user decision, Q5): in RUN with the pump ON, if `P_raw` stays below
`PLAUS_MIN_PRESSURE = 1.0` mbar(g) for more than `PLAUS_TIMEOUT_MS = 30 s`, trip with
`SENSOR_LOSS`. At the slowest Spec 1 ramp (10 mbar/min) a healthy rig passes 1 mbar(g) in about 6 s.
Residual risk: in those 30 s the fastest ramp (30 mbar/min) adds up to 15 mbar(g) unseen, which is
below the 20 mbar(g) limit and far below the 440 mbar pump deadhead (Table D layer 7). A fault in
RUN at steady 10 mbar(g) is caught by the drop check. Also note that the plausibility check trips
if the operator runs with FI-101 closed (no inflow) — expected behaviour.

## D. Resolved questions (answers 2026-11-15)

- Q1 latch kept; P5 uses serial `RESET` (README test procedure).
- Q2 `MIN_WARNING_SLOPE = 0.05` mbar/s, 3 consecutive samples; 14 mbar(g) rule immediate.
- Q3 states BOOT/IDLE/RUN/TRIPPED + flags; integrator freeze and hold last duty during a fault < 0.5 s.
- Q4 P > 31 valid → HARD_LIMIT; only P < −2 rejected.
- Q5 new-reference rule (10 samples within 0.5 mbar, only while TRIPPED; RESET still required) +
  low-pressure plausibility check (section C).
- Q6 report copied to `docs/` and git-ignored (contains names and invoices). PlatformIO installed in an
  isolated venv `%USERPROFILE%\.platformio\penv` (the official installer's location), because
  `C:\Python313` has no pip, so `pip install --user` was impossible.
- Python: `PYTHONHOME`/`PYTHONPATH` are not set (process, user, machine). `C:\Python313` contains only
  `python.exe` + DLLs, no `Lib`; the registry key `HKCU\Software\Python\PythonCore\3.13\PythonPath`
  points it to `...\AppData\Local\Programs\Python\Python313\Lib`, which has the stdlib but no
  `python.exe`. That split install causes the warning (`sys.prefix` falls back to the current directory).
  The venv is therefore built from `C:\Python313` (works, prints the warning). A repair/reinstall of
  Python 3.13 would fix it.

## E. Still open

- A13 (`ZERO` offset limit ±3 mbar) — confirm.
- A18 — a spare input pin for the t3 output read-back in I3a, if you want it in firmware.
- C1 — pump-off at 20 vs 29, waiting for CHE (`PUMP_OFF_AT_SOFT_LIMIT`).
- I1 / I6b (Table I) assume an LED toggled at t0 and a vent LED for the 240 fps video. No LED output exists
  in Figure 12; the DevKit onboard LED (GPIO2) would be a new pin. Not implemented without approval.
