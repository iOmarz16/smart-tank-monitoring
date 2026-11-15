# Assumptions, contradictions and open questions

Source: `TEAM_M021_Third_Report.pdf` (127 pages). Precedence: Ch. 5 + Table B + Fig. 12 > Ch. 4.
Status key: **OPEN** = waiting for the user · **ASSUMED** = decided, reversible · **CONFIRMED** = user approved.

## A. Contradictions found in the report

| # | Topic | Where | What conflicts | Handling |
|---|---|---|---|---|
| C1 | Pump off at 20 vs 29 | Spec 6 (p. 24) vs Table D (§5.1.2) | Spec 6: "injection stops and the vent opens" at 20. Table D layer 3 (20): vent 100 % open only; layer 4 (29): pump OFF. | `PUMP_OFF_AT_SOFT_LIMIT = true`. **RESOLVED: CHE confirmed pump OFF at 20 mbar(g)** (team update 2026-11-15). CONFIRMED |
| C2 | Soft limit latches vs auto re-close | Fig. 7 + §4.3.1.2 vs Table F test T3 (§5.2.1) | Fig. 7/§4.3.1.2: trip latches, reset only by operator. T3 (HYSYS R04): "open 100 % at ≥ 20, **re-close at ≤ 10**" (re-closed at 48.28 s), i.e. auto-recovery with hysteresis. Rig test P5 says "as T3". Was: latch kept (Q1). **Superseded by A30** (team decision 2026-11-15): a SOFT_LIMIT trip from RUN auto-clears at ≤ 10 mbar(g), at most 3 times in 5 min; trips from TEST, SENSOR_LOSS and HARD_LIMIT stay latched. CONFIRMED |
| C3 | E-stop | Eq. 8, Fig. 7 (E-stop state, rule 1) vs Table C | Eq. 8 includes `emergency_stop`; Table C: no E-stop, ON-OFF switch only. | Dropped from firmware (Table C wins). |
| C4 | Sensor and validity rule | §4.3.1.2 Eq. 9 vs Table C, §5.2.2 | Eq. 9 is for the I2C ABP2 (0–60). As built: analog QBM2030, broken wire reads 0 V → needs the 2 mbar drop check (§5.2.2). | User rule: range [-2, 31] + drop check. See Q4, Q5. |
| C5 | Vent actuator | §4.3.1.2 Eq. 13 (servo θ = u_open) vs Table C | Servo position command replaced by NO solenoid with time-proportioned PWM ≤ 10 Hz. | PWM; closed duty = 100 − u_open. |
| C6 | State machine | Fig. 7 vs user §2.6 | Fig. 7 states: INITIALIZATION → NORMAL CONTROL (auto, on "self-test OK + valid sample"), EARLY WARNING (state), SENSOR FAULT ("hold last safe command", returns to normal if valid < 0.5 s), PRESSURE TRIP, MANUAL E-STOP. No IDLE / START. User: BOOT → IDLE → RUN → TRIPPED. BOOT → IDLE → RUN → TRIPPED (+ TEST for the serial test mode); warning and sensor_fault are flags (Q3). CONFIRMED |
| C7 | Warning confirmation | §4.2.2 (Block 9 text, p. 50) vs §4.3.1.2 | Block 9 text: warn "only after **several consecutive** valid, sufficiently steep, positive-slope estimates". §4.3.1.2 rule has no confirmation count and no slope threshold value. 0.05 mbar/s, 3 consecutive samples for the slope rule; 14 mbar(g) rule immediate (Q2). CONFIRMED |
| C8 | PID vs PI | §4.3.1.2 Eq. 12 (Kp, Ki, Kd unassigned) vs §5.1 / Table 5 | As built: PI from HYSYS, Kc = 36, Ti = 8 s, Kd = 0. | PI, `u = Kc(e + Ts/Ti·Σe)`. |
| C9 | Power-loss behaviour of vent | §4.3.1.2 (servo not driven open on power loss) vs Table C/D | As built: NO valve vents on power loss. | Ch. 5 wins. |
| C10 | Passive relief | §4.2.1 Block 5, §4.3.1 (PSV-101 water seal at 30 mbar(g); factory safety valve kept) vs Table C, §5.1.1 | Table C: "No water seal". §5.1.1: the factory safety valve was removed from Port A. | **No passive relief exists on the prototype.** Nothing in the firmware, docs or reviews may count PSV-101 or a safety valve as a layer. Above the software trips, the only hard limits are the pump deadhead (440 mbar, Table D layer 7: the vessel stays safe) and the PT-101 overload (100 mbar: the sensor is at risk). CONFIRMED (user, 2026-11-15) |

## B. Assumptions

| # | Assumption | Report reference | Status |
|---|---|---|---|
| A1 | Trip reset condition = `P_raw < 20.0` mbar(g) AND sensor valid AND not stale AND serial `RESET`. So `RESET_MAX_PRESSURE = 20.0` (strict `<`). | §4.3.1.2 ("Reset requires pressure below 20 mbar(g), a valid non-stale sample, and deliberate local operator action"); Fig. 7 rule 7 | CONFIRMED (report rule). Since A30, the only other way out of TRIPPED is the SOFT_LIMIT auto-clear |
| A2 | `P_f` = first-order EMA, `α = 0.5` (lowest value the report allows). | §4.3.1.2 Eq. 11 ("α ≥ 0.5 … final α from bench data, Task C3") | ASSUMED |
| A3 | On trip, the valve LEDC channel is detached and GPIO26 is driven LOW directly, because an LEDC duty change only takes effect at the next PWM period (up to 100 ms later). | §5.2.2 Spec 6 / I3 | ASSUMED (implementation detail) |
| A4 | During a sensor fault shorter than 0.5 s (invalid sample, not yet stale), the PI integrator is frozen and the last valve duty is held. | Fig. 7 SENSOR FAULT "hold last safe command" | CONFIRMED (Q3) |
| A5 | First valid sample after boot (or after `ZERO`) has no "last valid" reference, so the drop check is skipped for it. | §5.2.2 | ASSUMED |
| A6 | Zero offset is in mbar, stored in NVS, default 0.0 if never set. `ZERO` is accepted only in IDLE with pump OFF and valve OPEN (vessel vented), after ≥ `ZERO_MIN_IDLE_S` = 60 s in IDLE, from the last 10 **consecutive valid IDLE** samples (any state change or invalid sample restarts the window) whose max − min ≤ `ZERO_MAX_SPREAD` = 0.3 mbar. Review M3: a zero taken during blow-down shifts every limit up by the same amount. | §5.1.1 step 8 | CONFIRMED (user, review M3) |
| A7 | QBM2030-30 range selector must be set to 3000 Pa (BOM lists 1000/1500/3000 Pa). README will tell the operator to check it. | Table B item 7 | ASSUMED |
| A8 | The 4 relay modules in Table B (item 13) are not used; Fig. 12 uses MOSFETs only. | Table B vs Fig. 12 | ASSUMED |
| A9 | `t_to_limit` reported as null/absent when slope ≤ `MIN_WARNING_SLOPE`. | §4.3.1.2 ("for a positive accepted slope") | ASSUMED |
| A10 | The ML early-warning model (§5.2.2, Table H) is out of scope for this step; firmware warning is the rule-based one. The dashboard leaves room to add it later. | §5.2.2 "Limitations and safety" | ASSUMED |
| A11 | Lag compensation, when enabled, uses the 5-sample regression slope of P_f; it applies to the soft-limit trip only, not the hard trip. | §5.2.2 (I6b fallback, P + 1 s·dP/dt) | ASSUMED |
| A12 | Valve PWM: 10 Hz, 14-bit LEDC (10-bit cannot reach 10 Hz from the 80 MHz APB clock; 14-bit can). | Table C (PWM ≤ 10 Hz) | ASSUMED |
| A13 | `ZERO` is refused if the measured offset exceeds ±3.0 mbar (10 % FS, `ZERO_MAX_ABS_OFFSET`): the vessel is probably not vented, or the range jumper is wrong. New constant, not from the report. | §5.1.1 step 8 | CONFIRMED. `ZERO FORCE` overrides it up to the absolute cap `ZERO_FORCE_MAX_ABS_OFFSET` = ±5.0 mbar (review M3; FORCE cannot override the cap, the dwell or the spread rule): prints `WARN`, persists `zforced` in NVS, `zero_forced=true` in every sample. A forced zero never allows `START` (A24) |
| A14 | `TEST DUTY <n>` sets the **energized (closed)** duty of PCV-101, the same quantity as `valve_duty`. `TEST VALVE ENERGIZE` = 100 %, `RELEASE` = 0 %. `TEST` (no argument) enters test mode, only from IDLE. | brief §2.7 | ASSUMED |
| A15 | Trip check also runs in BOOT and IDLE. No valid sample within 0.5 s of boot → TRIPPED / SENSOR_LOSS. | brief §2.3/§2.6, Fig. 7 | ASSUMED |
| A16 | With `PUMP_OFF_AT_SOFT_LIMIT = false`, a soft trip keeps the pump in its previous state (ON only if it was ON). Any later hard limit or sensor loss turns it OFF and escalates `trip_reason`. | Table D layers 3–4 | ASSUMED (flag is `true` today) |
| A17 | Replay: on every WebSocket (re)connect the whole ring buffer (1500 samples = 150 s) is re-sent with `replayed = true`; live samples sent ≥ 3 samples behind the newest are also flagged `replayed`. The dashboard deduplicates by `seq` (first copy wins). The ESP32 cannot know what the client already has, because the only inbound message is ping. Live samples are sent before replayed ones; the backlog is interleaved at `REPLAY_RATE_HZ` = 100 samples/s (user request). | brief §3, I2 | CONFIRMED |
| A18 | Trip timestamps: `trip_decision_us` = esp_timer when the trip is decided; `trip_output_us` = esp_timer right after `valveSafe()` + pump LOW. These are software timestamps; Table I's t3 uses a spare ESP32 input reading the MOSFET outputs, Optional read-back added (user-approved): GPIO32 = valve `OUT−`, GPIO33 = pump `OUT−` through 36k/10k and 10k/12k dividers, `READBACK_ENABLED = false` by default. Low-side modules assumed (input HIGH = output OFF; to be confirmed with a multimeter, README 3.1). t3 = latest OFF edge of the outputs that were ON at the decision; = decision time if none was ON; `READBACK_TIMEOUT` after 1 s. | §5.2.2 Fig. 16, I3 | CONFIRMED (polarity to verify on hardware) |
| A19 | **Superseded by review C1/M1.** Was: `P_raw` < 1.0 mbar(g) for 30 s in RUN. Now: the raw pin voltage `v_gpio_mv` < `OPEN_CIRCUIT_MV_THRESHOLD` for > 30 s while the pump is commanded ON, in **any** state (RUN, TEST, and TRIPPED if the pump were allowed there) → `SENSOR_LOSS`. Raw mV, so the zero offset cannot hide an open circuit. "Commanded ON" = the pump command written in the previous loop. Every completed ADC read counts, valid or not; the timer clears on a read at or above the threshold or when the pump is commanded OFF. `PLAUS_MIN_PRESSURE` was removed. | user answer Q5; review C1, M1 | CONFIRMED (user) |
| A20 | Task watchdog timeout is 1 s (IDF 4.4 accepts whole seconds only). A stall shorter than 1 s is not caught by the watchdog. Core-0 idle task is also monitored (core default). | brief §2.1, Spec 6 | ASSUMED |

## C. Sensor-loss blind spot and its mitigation

The drop check (§5.2.2) detects a broken PT-101 wire only when the pressure at that moment is
clearly above the open-circuit reading: the reading falls to 0 V, a drop > 2 mbar per sample. Near
0 mbar(g) the drop is too small, and the open-circuit reading is inside the valid range, so the
fault is **not detectable** by the validity rule. The worst case is RUN start-up (vessel near
0 mbar(g)) with a broken wire: the pump runs, the PI sees e ≈ −10 and keeps the vent closed, and
pressure rises unseen.

**ADC floor (review C1).** At 11 dB the ESP32 ADC cannot read below its floor: a 0 V input reads
about 75–150 mV (Espressif rate 11 dB from ~150 mV), i.e. **+1.1 to +2.3 mbar(g) before the zero
offset** with the as-built ÷5 module (A29; it was +1.0 to +1.9 with the 33k/10k divider). The first mitigation (P_raw < 1.0 mbar(g), old A19) therefore never fired while the zero
offset was 0.0 (first flash, nothing in NVS): the open circuit looks like a vented vessel at
~1.5 mbar(g). The floor also widens the drop-check blind spot from ~2 to ~3–4 mbar(g) true.

Mitigation (user decisions, Q5 and review C1/M1):
1. **Open-circuit check on the raw voltage** (A19): pump commanded ON (any state) and
   `v_gpio_mv` < `OPEN_CIRCUIT_MV_THRESHOLD` for > `PLAUS_TIMEOUT_MS` = 30 s → `SENSOR_LOSS`.
   The threshold (provisional 250 mV) is set from the bench open-circuit measurement
   (README 3.3). Constraint on it: at the slowest Spec 1 ramp (10 mbar/min) a healthy rig from
   0 mbar(g) must cross it well inside 30 s. With the ÷5 module (A29), 5 mbar(g) at 30 s = 333 mV,
   so keep it ≤ ~300 mV (4.5 mbar(g), crossed at 27 s). At 250 mV (3.75 mbar(g)) the slowest ramp
   crosses after ~22.5 s. (With the old 33k/10k divider the bound was ~350 mV.)
2. **START needs a valid, non-forced ZERO in NVS** (A24).

Residual risk: in those 30 s with an undetected open circuit the pump adds, unseen, up to 15 mbar(g)
at the fastest Spec 1 ramp (30 mbar/min), and up to ~24 mbar(g) at the pump's full 250 mL/min
(≈ 48 mbar/min with C = 5.2 std cm³/mbar), i.e. possibly **above the 20 mbar(g) limit**, which
the blind reading cannot see; the 30 s open-circuit trip then vents. **There is no passive relief (C10)**: the remaining hard limits are the pump deadhead
(440 mbar; the vessel stays safe) and the PT-101 overload (100 mbar; the sensor is at risk, not
reached in 30 s). A fault in RUN at steady 10 mbar(g) is caught by the drop check. The check also
trips, as expected, when the pump runs into a vessel that cannot pressurize: FI-101 closed, lid
open, HV-101 open, or TEST with the vent released and low inflow (open vent at 98.7 std cm³/min
settles near 4 mbar(g) ≈ 267 mV with the ÷5 module, **just above** the provisional 250 mV).

**Floating input (A32, OPEN).** The ÷5 module's bottom resistor pulls GPIO34 to 0 V only while the
module-to-ESP32 wire is intact. If that wire breaks, GPIO34 (no internal pull resistor) floats,
and the reading is undefined. It may look like a plausible, steady pressure that neither the drop
check nor the open-circuit check catches. The 33k/10k divider had the same weakness only if its
10 kΩ was away from the pin. To be measured in README 3.3 step 6; the fix is a pull-down from GPIO34
to GND (1 MΩ: ~0.6 % reading error against the module's ~6 kΩ source), a hardware change for the team.

## D. Resolved questions (answers 2026-11-15)

- Q1 latch kept; P5 uses serial `RESET`. Superseded by A30 for SOFT_LIMIT trips from RUN.
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

- ~~C1 — pump-off at 20 vs 29~~ resolved: CHE confirmed pump OFF at 20 mbar(g).
- **A32** GPIO34 floats if the module-to-ESP32 wire breaks (section C): measure (README 3.3
  step 6); a pull-down is a team decision.
- **A31** ADC read time of 32 reads: estimated, not measured. Read it from `STATUS` at bring-up.
- **A29** noise without the 100 nF: check the ZERO spread rule (≤ 0.3 mbar = 20 mV) on the rig.
- **A30 / P5 step 3**: there is no agreed way to cause a SOFT_LIMIT trip in RUN on the rig (the open
  vent out-vents the pump), so the auto-clear is tested only on the Python port so far.
- MOSFET module polarity (low-side) to be measured before the read-back is wired (A18).
- **A25** `OPEN_CIRCUIT_MV_THRESHOLD = 250` mV is provisional: set it from the bench open-circuit
  measurement (README 3.3) before any pressurized run.
- ~~A26 vs P2~~ resolved (user, 2026-11-15): `TEST PUMP ON <seconds>`, see A26. P2 at
  10 mbar/min (~120 s to 20 mbar(g)) runs with one command, e.g. `TEST PUMP ON 180`.
- **P1 BLOCKED pending CHE** (user decision, 2026-11-15) — Table G P1 holds 25 mbar(g), vent closed,
  pump off, for 10 min. The soft trip at 20 mbar(g) is enforced in every state, TEST included, and
  latches with the vent open, so P1 cannot reach 25 mbar(g). There is no passive relief on the
  prototype (C10). **No mode, flag or command will be added that raises or bypasses the 20 mbar(g)
  limit.** The team asks CHE to run the leak test below 20 mbar(g); P1 stays blocked until they answer.
- m5 DTR/RTS: verify on the real DevKit at bring-up that opening the monitor does not reset it (A28).
- Interrupt watchdog path not hooked (A27); accepted for now, revisit only if bring-up shows a need.

## F. Additions (answers of 2026-11-15, second round)

| # | Decision / assumption | Status |
|---|---|---|
| A21 | GPIO2 onboard LED, serial `LED MODE SAMPLE` (toggle at t0 = loop start, before the ADC read) or `LED MODE VENT` (ON while the vent is commanded **fully** open, i.e. de-energized; OFF during PWM). Default VENT, not persisted. Driven LOW first in `setup()`; GPIO2 strapping requires LOW at reset, ensured by the DevKit LED + resistor to GND. | CONFIRMED (user); VENT definition ASSUMED |
| A22 | Dashboard "LED sync" square mirrors `led_on` of each new live sample on arrival (not on the 100 ms render tick), so one 240 fps video gives t4 − t0 for I1. | ASSUMED |
| A23 | 36k/10k valve divider gives ≥ 2.475 V (ESP32 V_IH) only for a valve rail ≥ 11.4 V; the full 10.2–13.2 V valve range cannot satisfy both ≤ 3.0 V and ≥ V_IH with one ratio. Valid because Buck 2 is set to 12.0 V (Fig. 12 note 2). | ASSUMED |

## G. Pre-flash safety review (2026-11-15): changes

Review findings C1, M1–M3 and m1–m9. PSV-101 is not on the prototype (C10); the review text that
counted it as a layer was wrong and is corrected here.

| # | Decision / assumption | Status |
|---|---|---|
| A24 | `START` is refused unless a **valid, non-forced zero exists in NVS**. Valid at boot = key `zero` present, `zforced` false, finite, \|zero\| ≤ 3.0 mbar (`ZERO_MAX_ABS_OFFSET`). In a session, a normal `ZERO` sets it, `ZERO FORCE` clears it. The serial task writes the zero to NVS and reads it back; if that fails it tells the control task (`ZERO_UNSAVED`), which clears it again, so an offset that is active in RAM but not saved does not allow `START`. | CONFIRMED (user, review C1) |
| A25 | `OPEN_CIRCUIT_MV_THRESHOLD` = 250 mV, **provisional**, to be set to the bench open-circuit maximum + margin, ≤ ~300 mV with the ÷5 module (section C, A29). | OPEN (bench measurement) |
| A26 | TEST pump duration: `TEST PUMP ON [<seconds>]`, default `TEST_PUMP_DEFAULT_S` = 60 s, hard cap `TEST_PUMP_MAX_S` = 300 s. A duration < 1 or > 300 s is refused (`ERR`, pump unchanged); the supervisor checks the range again itself. When the time is up the control task switches the pump OFF and the console prints `EVENT TEST pump OFF`. All trips and the open-circuit check stay enforced. Replaces the earlier "every `TEST PUMP ON` restarts a 60 s window" rule. A new `TEST PUMP ON` while the pump is ON **replaces** the remaining time (does not add), so one command never runs the pump longer than 300 s. | Duration argument, default and cap CONFIRMED (user, 2026-11-15); replace-not-add ASSUMED |
| A27 | Watchdog freeze path (review M2). (a) `esp_task_wdt_isr_user_handler()` (weak hook in IDF 4.4.7 `libesp_system.a`; disassembly shows it is called after the task-name prints and before `abort()`) routes GPIO26 back from LEDC to the GPIO register and clears GPIO26/27. ROM function + register writes only, IRAM. It fires for every watched task (control task; core-0 idle task). (b) `partitions_nocoredump.csv` = Arduino `default.csv` without the 64 KB coredump partition (sdkconfig has `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y`), so a panic does not write flash before rebooting; other offsets unchanged, the NVS survives the re-flash. Not covered by the hook: the **interrupt watchdog** (300 ms, interrupts disabled for too long) and other panics go straight to the panic handler (backtrace over UART, ~tens of ms, then reboot), with outputs held until the reset. | CONFIRMED (user, review M2) |
| A28 | m2: when the valve LEDC pin is detached, the channel is parked at 0 % duty, so a later re-attach starts OPEN (`ledcAttachPin` re-applies the channel's current duty for up to one 100 ms period). m3: `esp_reset_reason()` is printed at boot (with a warning after a watchdog, panic or brownout reset, because the RAM trip latch is lost) and sent in every sample as `reset_reason`. m5: `monitor_dtr = 0` / `monitor_rts = 0`, so opening the PlatformIO monitor does not reset the board (to verify on the real DevKit; other serial programs may still toggle DTR/RTS). m6: `analogSetAttenuation(ADC_11db)` sets the global attenuation that `analogReadMilliVolts` characterizes with, as well as the per-pin one. m9: commands carry an id; a reply that arrives after the 1 s timeout is printed as `LATE` and still processed (a late `ZERO` is still saved); all reply text, including `STATUS`, is formatted by the serial task, so the control task no longer formats floats; `STATUS` adds V_gpio, zero_ok, idle time and the stack high-water marks (control, net, serial). | CONFIRMED (user) |

### Review findings documented without a code change

| # | Finding | Consequence / handling |
|---|---|---|
| m1 | Sensor-loss timing: `age > 500 ms` with a 100 ms loop and µs jitter, so the trip falls on the 5th or the 6th missed sample (0.5 or 0.6 s). | Both satisfy Spec 6 ("> 0.5 s"). Expect both values in I3b results; analyze against "pump OFF and vent open ≤ 1.0 s after the 0.5 s timeout". |
| m4 | NVS flash writes (`ZERO`, serial task, core 0) disable the flash cache on **both** cores, so the control loop stalls briefly (a sector erase can take tens to a few hundred ms). Only possible in IDLE (pump OFF, vent OPEN). Wi-Fi PHY calibration may also write NVS at boot, while the loop is in BOOT; a stall over 0.5 s then gives a SENSOR_LOSS trip at boot (safe direction, `RESET`). | Do not send `ZERO` during an I2 run; a loop period > 110 ms right after a `ZERO` is this effect, not a fault. |
| m7 | `read_ok` is always true (`analogReadMilliVolts` has no error return), so the NaN path is unused. A saturated ADC (~3100 mV) reads ~46 mbar(g) with the ÷5 module → HARD_LIMIT (safe direction). | Kept as a hook for a future sensor driver. |
| m8 | The soft trip acts on a single raw sample: one ADC spike ≥ 20 mbar(g) trips. Since A30 a spike in RUN auto-clears on the next samples (P near 10), but it uses one of the 3 auto-clears per 5 min. | Safe direction (nuisance trip, by design: the filter must not mask a rise). If nuisance trips or auto-clears appear on the rig, look at `v_gpio_mv` in the CSV before changing anything. |

## H. As-built update and trip recovery (team update 2026-11-15)

| # | Decision / assumption | Status |
|---|---|---|
| A29 | **PT-101 input as built** (overrides Figure 12): a "Voltage Sensor" module dividing by 5, no 33k/10k, no 100 nF. `SENSOR_DIVIDER_RATIO = 5.0` (replaces `DIVIDER_RATIO = 4.3`) in firmware, fake_rig and check_rules: `P_mbar = analogReadMilliVolts × 0.015 − zero_offset`. Pin max 2.0 V (10 V sensor). Pins unchanged (GPIO34 / 26 / 27), being verified with continuity by the user. | CONFIRMED (team) |
| A30 | **SOFT_LIMIT auto-clear.** A SOFT_LIMIT trip clears by itself when `P_raw ≤ SOFT_AUTOCLEAR_P` = 10.0 mbar(g) with a valid, non-stale sample, the trip is not latched, no trip condition is present in that sample, and a valid zero is saved. RUN then resumes with a **bumpless PI restart**: on the clear sample the integrator is set to `(u_open/Kc − e)·Ti/Ts` with u_open = 100 (vent fully open in TRIPPED) and is not integrated on that sample, so the output is continuous; the normal recursion runs from the next sample. Every auto-clear is logged (`EVENT AUTO-CLEAR ...` on serial; `auto_clears` and `trip_latched` in every sample, so in the CSV). **Latching:** after `SOFT_AUTOCLEAR_MAX` = 3 auto-clears within `SOFT_AUTOCLEAR_WINDOW_S` = 300 s, the next SOFT_LIMIT trip in that window latches (RESET needed). SENSOR_LOSS and HARD_LIMIT always latch. **User decision (2026-11-15): a SOFT_LIMIT trip that started in TEST always latches**, so the pump never restarts by itself after a manual test; the P5/I3a vent-closed ramp (TEST) still ends with `RESET`. ASSUMED by me (not specified by the team), each in the safe direction: (a) "3 times" = three auto-clears allowed, the 4th trip latches; (b) during an auto-clearable SOFT_LIMIT trip, P ≥ 29, a stale sensor or a rejected drop (> 2 mbar/sample) turns it into a latched HARD_LIMIT / SENSOR_LOSS, so a wire break during the trip (whose reading the re-reference rule could accept) cannot lead to an auto-restart; (c) `RESET` still works on an auto-clearable trip (→ IDLE, pump stays OFF) and clears the auto-clear history; (d) the window is counted at the moment of the trip. | CONFIRMED (team; TEST rule by user); (a)–(d) ASSUMED |
| A31 | `ADC_AVERAGE_READS` = 32 (was 8). Each `analogReadMilliVolts` does one ADC1 conversion plus `esp_adc_cal_characterize` (Arduino 2.0.17 `esp32-hal-adc.c`). **Not measured here (no board):** estimated ~20–60 µs per read, i.e. ~0.6–2 ms per loop, 1–2 % of the 100 ms budget. Measured every loop: `STATUS` prints `adc_us` (last) and `adc_us_max` (since boot). | CONFIRMED (team); timing ASSUMED until measured |
| A32 | GPIO34 floats if the module-to-ESP32 wire breaks (section C). | OPEN (bench measurement, team decision on a pull-down) |
