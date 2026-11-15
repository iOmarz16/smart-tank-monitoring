# Assumptions, contradictions and open questions

Source: `TEAM_M021_Third_Report.pdf` (127 pages). Precedence: Ch. 5 + Table B + Fig. 12 > Ch. 4.
Status key: **OPEN** = waiting for the user · **ASSUMED** = decided, reversible · **CONFIRMED** = user approved.

## A. Contradictions found in the report

| # | Topic | Where | What conflicts | Handling |
|---|---|---|---|---|
| C1 | Pump off at 20 vs 29 | Spec 6 (p. 24) vs Table D (§5.1.2) | Spec 6: "injection stops and the vent opens" at 20. Table D layer 3 (20): vent 100 % open only; layer 4 (29): pump OFF. | `PUMP_OFF_AT_SOFT_LIMIT = true` (user instruction). Commented in code. Awaiting CHE. |
| C2 | Soft limit latches vs auto re-close | Fig. 7 + §4.3.1.2 vs Table F test T3 (§5.2.1) | Fig. 7/§4.3.1.2: trip latches, reset only by operator. T3 (HYSYS R04): "open 100 % at ≥ 20, **re-close at ≤ 10**" (re-closed at 48.28 s), i.e. auto-recovery with hysteresis. Rig test P5 says "as T3". | **OPEN — Q1.** |
| C3 | E-stop | Eq. 8, Fig. 7 (E-stop state, rule 1) vs Table C | Eq. 8 includes `emergency_stop`; Table C: no E-stop, ON-OFF switch only. | Dropped from firmware (Table C wins). |
| C4 | Sensor and validity rule | §4.3.1.2 Eq. 9 vs Table C, §5.2.2 | Eq. 9 is for the I2C ABP2 (0–60). As built: analog QBM2030, broken wire reads 0 V → needs the 2 mbar drop check (§5.2.2). | User rule: range [-2, 31] + drop check. See Q4, Q5. |
| C5 | Vent actuator | §4.3.1.2 Eq. 13 (servo θ = u_open) vs Table C | Servo position command replaced by NO solenoid with time-proportioned PWM ≤ 10 Hz. | PWM; closed duty = 100 − u_open. |
| C6 | State machine | Fig. 7 vs user §2.6 | Fig. 7 states: INITIALIZATION → NORMAL CONTROL (auto, on "self-test OK + valid sample"), EARLY WARNING (state), SENSOR FAULT ("hold last safe command", returns to normal if valid < 0.5 s), PRESSURE TRIP, MANUAL E-STOP. No IDLE / START. User: BOOT → IDLE → RUN → TRIPPED. | **OPEN — Q3.** |
| C7 | Warning confirmation | §4.2.2 (Block 9 text, p. 50) vs §4.3.1.2 | Block 9 text: warn "only after **several consecutive** valid, sufficiently steep, positive-slope estimates". §4.3.1.2 rule has no confirmation count and no slope threshold value. | **OPEN — Q2.** |
| C8 | PID vs PI | §4.3.1.2 Eq. 12 (Kp, Ki, Kd unassigned) vs §5.1 / Table 5 | As built: PI from HYSYS, Kc = 36, Ti = 8 s, Kd = 0. | PI, `u = Kc(e + Ts/Ti·Σe)`. |
| C9 | Power-loss behaviour of vent | §4.3.1.2 (servo not driven open on power loss) vs Table C/D | As built: NO valve vents on power loss. | Ch. 5 wins. |

## B. Assumptions

| # | Assumption | Report reference | Status |
|---|---|---|---|
| A1 | Trip reset condition = `P_raw < 20.0` mbar(g) AND sensor valid AND not stale AND serial `RESET`. So `RESET_MAX_PRESSURE = 20.0` (strict `<`). | §4.3.1.2 ("Reset requires pressure below 20 mbar(g), a valid non-stale sample, and deliberate local operator action"); Fig. 7 rule 7 | ASSUMED (report gives exact rule) — depends on Q1 |
| A2 | `P_f` = first-order EMA, `α = 0.5` (lowest value the report allows). | §4.3.1.2 Eq. 11 ("α ≥ 0.5 … final α from bench data, Task C3") | ASSUMED |
| A3 | On trip, the valve LEDC channel is detached and GPIO26 is driven LOW directly, because an LEDC duty change only takes effect at the next PWM period (up to 100 ms later). | §5.2.2 Spec 6 / I3 | ASSUMED (implementation detail) |
| A4 | During a sensor fault shorter than 0.5 s (invalid sample, not yet stale), the PI integrator is frozen and the last valve duty is held. | Fig. 7 SENSOR FAULT "hold last safe command" | ASSUMED — see Q3 |
| A5 | First valid sample after boot (or after `ZERO`) has no "last valid" reference, so the drop check is skipped for it. | §5.2.2 | ASSUMED |
| A6 | Zero offset is in mbar, stored in NVS, default 0.0 if never set. `ZERO` is accepted only in IDLE with pump OFF and valve OPEN (vessel vented). | §5.1.1 step 8 | ASSUMED |
| A7 | QBM2030-30 range selector must be set to 3000 Pa (BOM lists 1000/1500/3000 Pa). README will tell the operator to check it. | Table B item 7 | ASSUMED |
| A8 | The 4 relay modules in Table B (item 13) are not used; Fig. 12 uses MOSFETs only. | Table B vs Fig. 12 | ASSUMED |
| A9 | `t_to_limit` reported as null/absent when slope ≤ `MIN_WARNING_SLOPE`. | §4.3.1.2 ("for a positive accepted slope") | ASSUMED |
| A10 | The ML early-warning model (§5.2.2, Table H) is out of scope for this step; firmware warning is the rule-based one. The dashboard leaves room to add it later. | §5.2.2 "Limitations and safety" | ASSUMED |
| A11 | Lag compensation, when enabled, uses the 5-sample regression slope of P_f; it applies to the soft-limit trip only, not the hard trip. | §5.2.2 (I6b fallback, P + 1 s·dP/dt) | ASSUMED |
| A12 | Valve PWM: 10 Hz, 14-bit LEDC (10-bit cannot reach 10 Hz from the 80 MHz APB clock; 14-bit can). | Table C (PWM ≤ 10 Hz) | ASSUMED |

## C. Open questions (blocking step 2)

**Q1 — Soft limit: latch or auto re-close?** (C2)
Fig. 7 / §4.3.1.2 say the trip latches until operator reset. HYSYS T3, which rig P5 repeats, shows
the vent re-closing automatically at ≤ 10 mbar(g). Proposal: keep the latch (your brief + Fig. 7);
T3/P5 then needs the operator to send `RESET`, and P5's "re-close" is not tested on the rig.

**Q2 — `MIN_WARNING_SLOPE` and confirmation count.** (C7)
The report gives no number. Proposal: `MIN_WARNING_SLOPE = 0.05 mbar/s` (3 mbar/min, below the
slowest 10 mbar/min = 0.167 mbar/s ramp of Spec 1) and `WARNING_CONFIRM_SAMPLES = 3` consecutive
samples, as Block 9 asks. The 14 mbar(g) fixed warning is not delayed by the confirmation.

**Q3 — States.** (C6)
Proposal: BOOT → IDLE → RUN → TRIPPED (your list), plus `warning` and `sensor_fault` as flags
rather than states, with the Fig. 7 behaviour (A4). RUN is entered only by serial `START`, and only
with a valid sensor. Is that acceptable as the Figure 7 mapping, given Fig. 7 enters control automatically?

**Q4 — Over-range vs the [-2, 31] range check.**
The divider allows readings above 31 mbar(g) if the sensor outputs > 10.3 V, or if the zero offset
is negative. Proposal: P > 31 is **valid** and trips HARD_LIMIT; only P < −2 fails the range check.

**Q5 — Recovering from a drop-check rejection.**
After a broken wire (or a real fast drop), every later sample is compared with an old "last valid"
value and is rejected forever, so the board stays in SENSOR_LOSS until reboot. Proposal: after the
trip has latched, accept a new reference after 10 consecutive samples (1 s) that agree within
0.5 mbar of each other. `RESET` is still needed to leave TRIPPED. Note also: a broken wire at
P < 2 mbar(g) is not detectable by this rule.

**Q6 — Housekeeping.**
- The report is at `C:\Users\amory\Downloads\TEAM_M021_Third_Report.pdf`, not in `docs/`. Copy it in
  and commit it (4.2 MB), or copy it and add it to `.gitignore`?
- PlatformIO CLI is not installed (needed for `pio run` in step 2). Install it with
  `py -3.13 -m pip install --user platformio`? `pio run` then downloads the ESP32 toolchain (~1 GB).
- `C:\Python313` prints "Could not find platform independent libraries" on every start (broken
  `PYTHONHOME`/install?). It still runs. The dashboard will use a project venv in `dashboard/.venv`.
