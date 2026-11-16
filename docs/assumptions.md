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
   The threshold is **180 mV, measured** (A25) from the bench open-circuit measurement
   (README 3.3). Constraint on it: at the slowest Spec 1 ramp (10 mbar/min) a healthy rig from
   0 mbar(g) must cross it well inside 30 s. With the ÷5 module (A29), 5 mbar(g) at 30 s = 333 mV,
   so keep it ≤ ~300 mV (4.5 mbar(g), crossed at 27 s). At 180 mV a vented rig already reads ~142 mV
   (the 2.13 mbar zero offset is the ADC floor), so the slowest ramp
   crosses after a few seconds (~0.6 mbar(g) above vented). (With the old 33k/10k divider the bound was ~350 mV.)
2. **START needs a valid, non-forced ZERO in NVS** (A24).

Residual risk: in those 30 s with an undetected open circuit the pump adds, unseen, up to 15 mbar(g)
at the fastest Spec 1 ramp (30 mbar/min), and up to ~24 mbar(g) at the pump's full 250 mL/min
(≈ 48 mbar/min with C = 5.2 std cm³/mbar), i.e. possibly **above the 20 mbar(g) limit**, which
the blind reading cannot see; the 30 s open-circuit trip then vents. **There is no passive relief (C10)**: the remaining hard limits are the pump deadhead
(440 mbar; the vessel stays safe) and the PT-101 overload (100 mbar; the sensor is at risk, not
reached in 30 s). A fault in RUN at steady 10 mbar(g) is caught by the drop check. The check also
trips, as expected, when the pump runs into a vessel that cannot pressurize: FI-101 closed, lid
open, HV-101 open, or TEST with the vent released and low inflow (open vent at 98.7 std cm³/min
settles near 4 mbar(g) ≈ 400 mV with the ÷5 module and the 2.13 mbar zero offset, well above 180 mV).

**Floating input (A32, DECIDED: 1 MΩ pull-down).** The ÷5 module's bottom resistor pulls GPIO34 to 0 V only while the
module-to-ESP32 wire is intact. If that wire breaks, GPIO34 (no internal pull resistor) floats,
and the reading is undefined. It may look like a plausible, steady pressure that neither the drop
check nor the open-circuit check catches. The 33k/10k divider had the same weakness only if its
10 kΩ was away from the pin. To be measured in README 3.3 step 6; the fix is a pull-down from GPIO34
to GND (1 MΩ: ~0.6 % reading error against the module's ~6 kΩ source), a hardware change for the team.
**Team decision (2026-11-15): a 1 MΩ pull-down from GPIO34 to GND will be added.** A broken
module-to-ESP32 wire then reads ~0 V, the same as a broken sensor wire, so the drop check and the
open-circuit check apply. Side effect: the reading becomes ~0.6 % low, assuming a typical 30k/7.5k
module (Thevenin ~6 kΩ; the real module values are not known). That puts the 20 mbar(g) trip at
~20.1 true and the 29 at ~29.2. The zero does not remove a gain error: do the two-point U-tube check (P0) after
fitting it. `SENSOR_DIVIDER_RATIO` stays 5.0 unless the team decides to correct it from that check.

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
- **A32** decided: 1 MΩ pull-down GPIO34 → GND to be fitted. Then verify (README 3.3 step 6) that
  unplugging the module-to-GPIO34 wire reads like the sensor-wire break, and redo the two-point
  U-tube check (~0.6 % gain change).
- **A33** heap: read the `HEAP` boot lines on the real board; ≥ 40 KB must remain after Wi-Fi
  (ring now 72 KB at 48 B/sample since A34, was 66 KB).
- **A34** leak model constants are provisional until the ADC-floor zero check; P_comp is CSV only (A35).
- **A35** SIM RAMP revised (valve closed for the whole run, PI logged only) after the rig run tripped
  SENSOR_LOSS at P_sim 15.05. Re-run SIM RAMP 10/20/30 on the rig: each must end in SOFT_LIMIT at 20.
- ~~A34 / A25 note~~ **withdrawn** (team correction, 2026-11-16): my ~227 mV plateau estimate left
  out the 2.13 mbar zero offset. On the rig P_raw = 2.6 read V_gpio = 314 mV; the plateau sits at
  ~350 mV, and the team's 300 s pump-ON run on this firmware did not trip SENSOR_LOSS.
- ~~A25 threshold~~ **resolved** (team, 2026-11-16): `OPEN_CIRCUIT_MV_THRESHOLD` = 180 mV, measured
  (see A25). The module-to-GPIO34 wire case with the 1 MΩ pull-down (README 3.3 step 6) is still
  open under A32.
- **A31** ADC read time of 32 reads: estimated, not measured. Read it from `STATUS` at bring-up.
- **A29** noise without the 100 nF: check the ZERO spread rule (≤ 0.3 mbar = 20 mV) on the rig.
- **A30 / P5 step 3**: there is no agreed way to cause a SOFT_LIMIT trip in RUN on the rig (the open
  vent out-vents the pump), so the auto-clear is tested only on the Python port so far.
- MOSFET module polarity (low-side) to be measured before the read-back is wired (A18).
- ~~A26 vs P2~~ resolved (user, 2026-11-15): `TEST PUMP ON <seconds>`, see A26. P2 at
  10 mbar/min (~120 s to 20 mbar(g)) runs with one command, e.g. `TEST PUMP ON 180`.
- **P1 BLOCKED pending CHE** (user decision, 2026-11-15) — Table G P1 holds 25 mbar(g), vent closed,
  pump off, for 10 min. The soft trip at 20 mbar(g) is enforced in every state, TEST included, and
  latches with the vent open, so P1 cannot reach 25 mbar(g). There is no passive relief on the
  prototype (C10). **No mode, flag or command will be added that raises or bypasses the 20 mbar(g)
  limit.** The team asks CHE to run the leak test below 20 mbar(g); P1 stays blocked until they answer.
- ~~m5 DTR/RTS~~ resolved (team, 2026-11-15): opening the serial monitor did **not** reset the
  board (A28).
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
| A25 | `OPEN_CIRCUIT_MV_THRESHOLD` = **180 mV, measured** (bench open-circuit test, README 3.3, reported 2026-11-16): 0 V at the sensor input reads a steady 142.0 mV at GPIO34, max noise 147.7 mV → 32 mV margin. Rule kept: max + margin, ≤ ~300 mV with the ÷5 module (section C, A29). Was 250 mV provisional. Healthy rig: vented ~142 mV (= the 2.13 mbar zero offset), pump-ON plateau with the vent closed ~350 mV. | CONFIRMED (team, measured); module-to-GPIO34 wire case open under A32 |
| A26 | TEST pump duration: `TEST PUMP ON [<seconds>]`, default `TEST_PUMP_DEFAULT_S` = 60 s, hard cap `TEST_PUMP_MAX_S` = 300 s. A duration < 1 or > 300 s is refused (`ERR`, pump unchanged); the supervisor checks the range again itself. When the time is up the control task switches the pump OFF and the console prints `EVENT TEST pump OFF`. All trips and the open-circuit check stay enforced. Replaces the earlier "every `TEST PUMP ON` restarts a 60 s window" rule. A new `TEST PUMP ON` while the pump is ON **replaces** the remaining time (does not add), so one command never runs the pump longer than 300 s. | Duration argument, default and cap CONFIRMED (user, 2026-11-15); replace-not-add ASSUMED |
| A27 | Watchdog freeze path (review M2). (a) `esp_task_wdt_isr_user_handler()` (weak hook in IDF 4.4.7 `libesp_system.a`; disassembly shows it is called after the task-name prints and before `abort()`) routes GPIO26 back from LEDC to the GPIO register and clears GPIO26/27. ROM function + register writes only, IRAM. It fires for every watched task (control task; core-0 idle task). (b) `partitions_nocoredump.csv` = Arduino `default.csv` without the 64 KB coredump partition (sdkconfig has `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y`), so a panic does not write flash before rebooting; other offsets unchanged, the NVS survives the re-flash. Not covered by the hook: the **interrupt watchdog** (300 ms, interrupts disabled for too long) and other panics go straight to the panic handler (backtrace over UART, ~tens of ms, then reboot), with outputs held until the reset. | CONFIRMED (user, review M2) |
| A28 | m2: when the valve LEDC pin is detached, the channel is parked at 0 % duty, so a later re-attach starts OPEN (`ledcAttachPin` re-applies the channel's current duty for up to one 100 ms period). m3: `esp_reset_reason()` is printed at boot (with a warning after a watchdog, panic or brownout reset, because the RAM trip latch is lost) and sent in every sample as `reset_reason`. m5: `monitor_dtr = 0` / `monitor_rts = 0`, so opening the PlatformIO monitor does not reset the board (**verified on the real DevKit 2026-11-15**: opening the monitor did not reset it; other serial programs may still toggle DTR/RTS). m6: `analogSetAttenuation(ADC_11db)` sets the global attenuation that `analogReadMilliVolts` characterizes with, as well as the per-pin one. m9: commands carry an id; a reply that arrives after the 1 s timeout is printed as `LATE` and still processed (a late `ZERO` is still saved); all reply text, including `STATUS`, is formatted by the serial task, so the control task no longer formats floats; `STATUS` adds V_gpio, zero_ok, idle time and the stack high-water marks (control, net, serial). | CONFIRMED (user) |

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
| A32 | GPIO34 floats if the module-to-ESP32 wire breaks (section C). Fix: **1 MΩ pull-down GPIO34 → GND** (team decision 2026-11-15). Reading ~0.6 % low afterwards (estimate); check with the two-point U-tube test. No firmware change. | CONFIRMED (team); to fit and verify on the rig |
| A33 | **Memory fix.** On the first flash the main firmware stopped with `FATAL ring buffer allocation failed - outputs held safe`. Cause: one 144 KB `calloc` (1500 × 96 B `Sample`) is larger than the largest free block of the ESP32 heap, which is split into regions (typically ~110 KB). This was not measured on the board; it follows from the code and the ESP32 heap layout. Fix: (a) samples are stored **packed in 44 B** (48 B since A34): `seq` implicit; booleans and enums in one 32-bit bitfield; trip times as a 48-bit decision time plus two deltas (exact µs); `integ`, `t_esp_ms` and `loop_period_us` exact; the other floats as scaled 16-bit integers: `v_gpio_mv` 0.1 mV, `P_raw`/`P_f` 0.002 mbar (±65.5), `slope` 0.001 mbar/s, `t_to_limit` 0.02 s, `u_open`/`valve_duty` 0.01 %, `zero_offset` 0.0002 mbar; NaN kept. (b) The buffer is split into 10 blocks of 150 samples (6.6 KB each, 66 KB total). `RING_SAMPLES` stays **1500** (150 s; the 900 minimum is a `static_assert`). The quantization affects **logged values only**: the supervisor (PI, trips, warning) works on floats and never reads the ring. I5 error from it: about 0.04 % of duty (P_f 0.001 mbar × Kc 36), well inside the 1 % limit. The fail-safe is kept: if any block fails, all are freed and `haltSafe()` holds the outputs safe. At boot the console prints `HEAP ...` lines (free, largest block, min free): before and after the ring, before Wi-Fi, and after Wi-Fi + WebSocket. It warns if less than `HEAP_MIN_FREE_AFTER_WIFI` = 40 KB is free after Wi-Fi. A pack/unpack self-test runs at boot (no host compiler for a unit test). If it fails, it prints a WARNING only, because control never reads the ring. | CONFIRMED (user: ≥ 900 samples, ≥ 40 KB after Wi-Fi); heap figures to read from the board |

## I. Team leak model, log and display only (team update 2026-11-15)

Source: `docs/rig_corrections.h` and `docs/example_integration.ino` (team files, written for a
0.2 s logging sketch, not this firmware; copied unchanged into `docs/`).

**Team assumption text** (`rig_corrections.h`, section 4, verbatim):

> P_comp = real corrected pressure + all pressure lost to the measured leak since the pot was last
> empty.
>
> !! This is an ASSUMPTION, not a measurement. It is NOT the pot pressure.
> !! It keeps growing while the pump runs at its plateau (~3.4 mbar), because it assumes the pump
> could keep supplying the leaked air. Report it with the real pressure next to it. Never use it
> for the safety trip.
>
> Rules: accumulates only while the vent valve is CLOSED (venting is intentional, not a leak);
> resets when the pump is OFF and the real pressure is back near zero.

**P_comp is not used as evidence for any specification; S1, S3 and IS1–IS3 are assessed on
measured pressure only. The pump's ability to reach 20 mbar(g) was not verified.**

| # | Decision / assumption | Status |
|---|---|---|
| A34 | **Leak model, LOG AND DISPLAY ONLY.** The control task computes it once per loop, **after** `Supervisor::step()` (`src/leak_model.h`, Python port `LeakModel` in `tools/fake_rig.py`). It writes only to the logged sample: `P_corr`, `P_comp_assumed`, `leak_rate` (JSON, CSV, `TEST` serial line). Trips, warning, PI and the plausibility checks are unchanged and run on the measured `P_raw` / `P_f` only. `tools/check_rules.py` section 13 checks this: the supervisor sources contain no leak-model reference, only the control task includes `leak_model.h` and only after `sup.step()`, the three results only go into the Sample, the fake-rig Supervisor never sees the model, P_comp above 29 mbar(g) at a measured 5 mbar(g) causes no trip or warning, and the dashboard status code never reads it. **Model** (team): `P_corr = P_f + PUMP_ON_OFFSET` while the pump runs; `leak_rate = k·√P_corr` (0 at P_corr ≤ 0), `k = K_LEAK_PUMP_ON` = 0.045 mbar^0.5/s with the pump ON (pot leak only, test B), `K_LEAK_PUMP_OFF` = 0.28 with the pump OFF (pot leak + backflow through the pump line, test A); `P_comp = P_corr + lost`, `lost += leak_rate·TS_S·(closed duty/100)`; `lost = 0` when the pump is OFF and `P_corr < P_COMP_RESET_P` = 0.05 mbar(g). **Constants** in `config.h`, all marked "measured 2026-11-15, provisional until the ADC-floor zero check": `PUMP_ON_OFFSET` 0.19 mbar, `K_LEAK_PUMP_ON` 0.045, `K_LEAK_PUMP_OFF` 0.28, `P_COMP_RESET_P` 0.05. **Period:** the per-step loss uses `TS_S` = 0.1 s (the team file uses 0.2 s, its log period, which would double P_comp here); a `static_assert` ties it to `CONTROL_PERIOD_MS`. The k values are per second and the offset is per reading, so they do not change with the period. **User decisions (2026-11-15):** (a) PCV-101 is PWM-driven, not on/off: each step's loss is **weighted by the energized (closed) duty**, 100 % = whole step, safe/open = nothing; (b) the pump state for the offset and for k is the command **written in the previous loop**, i.e. the one running while the reading was taken (same rule as A19); it differs from the logged `pump_on` only on the step where the pump switches. ASSUMED by me: (c) the duty weighting uses the previous loop's applied duty as well (the step that ends at this reading; 0 when the valve was safe); (d) before the first valid sample (`P_f` = NaN) all three fields are null and the accumulated loss is kept; while a sample is invalid the model runs on the held `P_f`; (e) the nominal `TS_S` is used, not the measured loop period; (f) the team's on-board `LeakEstimator` (`EVENT LEAK k=...`) and `predictPressureOff` / `timeToDropOff` were **not** ported (not requested). **Ring:** the packed sample grows from 44 B to **48 B** (1500 × 48 B = 72 KB, 10 blocks of 7.2 KB; was 66 KB). `P_comp_assumed` is 16-bit at 0.02 mbar (±655 mbar, saturates beyond; it grows without bound while the pump runs with the vent closed); `leak_rate` is 16-bit at 0.0001 mbar/s; `P_corr` is not stored but rebuilt from `P_f` and a 1-bit flag (offset applied), exactly as computed (changed by A35: `P_corr` is stored, `leak_rate` is rebuilt). The boot self-test covers the new fields. **Dashboard: removed by A35 (CSV only now).** Was: dashed grey line "assumed (no-leak estimate)" plus a numeric readout labelled "not measured". The y-axis scales on the measured pressure only, so a P_comp above the axis is clipped instead of squashing the real pressure. The status colour, warning and trip display never use it. | CONFIRMED (team model, user rules and decisions a, b); c–f ASSUMED |

## J. SIM RAMP test mode (team request 2026-11-16)

For trend testing and learning; the team will compare SIM runs with real runs later.

| # | Decision / assumption | Status |
|---|---|---|
| A35 | **SIM RAMP.** Serial `SIM RAMP <10\|20\|30>` (mbar/min), from IDLE or TEST; `SIM EXIT` (or `STOP`) → IDLE, pump OFF, valve released. New state `SIM` (enum value 5, appended so logged values of the other states do not change). **While in SIM the simulated pressure is THE pressure** for P_raw, P_f, slope, time-to-limit, warning, PI and the 20/29 mbar(g) trips, and on the dashboard (main number, status colour, chart). No banner or special styling: only the state field shows `SIM`. **Real protection stays on, never bypassed:** a trip also fires when the REAL pressure reaches 20 (SOFT_LIMIT) or 29 (HARD_LIMIT), and the real stale (0.5 s), drop and open-circuit (180 mV / 30 s) checks run unchanged on the sensor. `RESET` always checks the REAL pressure. **Implementation:** the real-sensor path (`validate()`) is unchanged and keeps its own `p_real_`; a separate control-pressure stage feeds P_f / slope / warning / PI from the real pressure (outside SIM, so nothing changes there; `check_rules.py` checks P_raw == P_real outside SIM) or from the ramp (in SIM). The trip logic checks the control pressure **and** the real pressure. **User decisions (2026-11-16):** (a) **revised after the rig run (2026-11-16):** pump ON and **PCV-101 ENERGIZED (closed) at 100 % for the whole run**, as the request specified. The PI runs on the simulated P_f and `u_open` / `integ` are logged as computed, but it does **not** drive the valve (so in SIM `valve_duty` = 100, not 100 − u_open). On any trip the normal TRIPPED outputs apply (pump OFF, valve released). *Superseded first version:* the PI drove the real valve from P_sim. **Rig result (30 mbar/min):** warning fired correctly at P_sim 14.1 (t_to_limit ~12 s), but above 10 mbar(g) the PI opened the vent fully (u_open 100, duty 0 %), the real pressure vented to 0 (V_gpio 142 mV) and the open-circuit check tripped SENSOR_LOSS at P_sim 15.05 instead of SOFT_LIMIT at 20; (b) the ramp **starts at the current real P_f** (no jump), and **any trip in SIM latches** (like TEST) and ends SIM: from the next sample the pressure is the real one again; (c) **entry gate as START** (A24): valid non-stale real sample and a valid, non-forced zero in NVS. ASSUMED by me: (d) the simulated pressure is `P0 + rate × elapsed time` on the ESP32 clock (exact ramp, no noise), so the fitted slope matches the target almost exactly; (e) the step that executes `SIM RAMP` is still logged as real (`simulated` = 0, state `SIM`); the step that decides a trip is still simulated; (f) a real sensor fault shorter than 0.5 s freezes the PI and holds the duty, as in RUN (A4); (g) the open-circuit timer is not reset on entry (pump time in TEST before SIM keeps counting); (h) backstop: SIM ends (→ IDLE) after `TEST_PUMP_MAX_S` = 300 s; not reachable in practice, since 10 mbar/min from the lowest valid start (−2 mbar(g)) reaches 20 mbar(g) in 132 s (`static_assert`); (i) the leak model (A34) uses the REAL filtered pressure in SIM too; (j) **logging:** JSON/CSV fields `P_real` (measured), `simulated` (CSV 0/1), `sim_rate`, next to P_raw / P_f. The packed ring sample stays **48 B**: `P_corr` is now stored and `leak_rate` rebuilt from it; `P_real` is rebuilt in SIM from `v_gpio_mv` and `zero_offset` (same formula as the supervisor; `read_ok` is always true on the ESP32), outside SIM it is `P_raw`; `simulated` and `sim_rate` use the last 3 bits of the flag word. (k) Dashboard: `P_comp_assumed` removed from the page (still in the CSV). (l) `analyze_log.py`: per SIM run (contiguous `simulated` = 1): least-squares slope of the simulated P_raw vs target ±10 %, warning lead before 20 mbar(g) (≥ 10 s), trip decision → output time; I5 stays RUN-only. **Python port results** (`check_rules.py` section 15, real input 314 mV / zero 2.13 as on the rig): warning lead 36.0 s at 10, 17.9 s at 20, 11.9 s at 30 mbar/min. **Regression check** (`check_rules.py`, rig case): the real reading follows the valve (closed + pump ON → ~350 mV plateau, open → 142 mV, 5 s lag, zero 2.13 mbar); at 10, 20 and 30 mbar/min, from the plateau and from a vented start, every run must end in a latched SOFT_LIMIT at simulated 20 mbar(g) with pump OFF and valve released. With the superseded PI-drives-valve version this case fails with SENSOR_LOSS (at 10 mbar/min in this simple model; the rig, which rises more slowly, also failed at 30), and the "duty 100 % in SIM" check fails at every rate. | CONFIRMED (team request; user decisions a–c); d–l ASSUMED |

## K. DEMO mode for the supervisor meeting (team request 2026-11-16)

Shows how each specification of Table 1 (Section 2.1.7) will be verified on the dashboard,
against `tools/fake_rig.py`. **No firmware change.** Demo results come from the simulator and
are **not evidence** for any specification; the rig tests of Table I (and Table G) are.

| # | Decision / assumption | Status |
|---|---|---|
| A36 | **DEMO mode.** `run_demo.bat` → `app.py --demo`, which starts `fake_rig.py --demo` itself (127.0.0.1 only) and opens the browser. Header tag "DEMO · simulated rig". **Control path:** the Scenario dropdown, the cards, the demo speed and "Reset demo" go from the browser to the local backend and from there to the **simulator's own control channel** (127.0.0.1:8766, a port the firmware does not have). `--demo` refuses any other rig URL; without `--demo` every browser message is ignored, as before; the rig link still carries pings only (`check_rules.py` section 16). The scenario scripts (`tools/demo_director.py`) act only like an operator (serial commands through the normal command queue) or a test bench (plant inflow, blocked vent, broken PT-101 wire, gas slug, simulated Wi-Fi outage); they never write Supervisor state, limits or outputs. **Reset demo** restarts the simulator process and starts a fresh dashboard session. **Logs:** one CSV per scenario run in `dashboard/logs/demo/` (plus the simulator's console log), never with rig logs; demo-only columns `demo_scenario`, `demo_phase`, `demo_t_s`, `demo_speed`, `demo_mark`. **Spec texts** from Table 1 / Section 2.1.7 (the ≤/≥ signs, lost in the PDF text, restored from Section 2.1.7 and Table I); S1 as worded by the team. **Scenarios and criteria** (user request; values marked * ASSUMED by me): **S1** first card and first scenario: TEST mode (now ported to the fake rig, same rules as `supervisor.cpp`), vent energized, pump ON, ramps 10 → 20 → 30 mbar/min back to back, each from vented (*P_f ≤ 0.2 mbar(g) for 1 s), stopped at 15 mbar(g) (PT-101 reading), vented between ramps; *inflows of Table F test T1 (49.3 / 98.7 / 148.0 std cm³/min), which in the plant model (C = 5.20 std cm³/mbar) give the report's 9.48 / 18.97 / 28.45 mbar/min, i.e. slopes inside but not on the target; *small noise: PT-101 noise 0.1 mbar plus 1 % pump-flow scatter per ramp; least-squares slope of PT-101 samples between 0 and 15 mbar(g) against **process time**; bands 9–11 / 18–22 / 27–33; headspace 5.0 L shown as a fixed value "set by CHE". **Demo speed 1×/2×/4×** scales the plant's process time in **S1 only** (the slope is judged on process time, so the result does not depend on it); every other scenario measures real-time behaviour and always runs at 1×. **S4:** hold at 10; live latency median / p95 / max; *PASS after 300 live updates (30 s) all ≤ 1.0 s (I1 on the rig: 10 min). **S5:** hold at 10, *20 s Wi-Fi outage starting 10 s in; *PASS when the outage is over, missing seq = 0, mean loop period 100 ± 1 ms, none > 110 ms and ≥ 5 samples in every full 1 s window of ESP32 time (the I2 criteria). **S6a / IS3:** hold at 10, then vent blocked at 148 std cm³/min (P5 / I3a) up to the 20 mbar(g) trip; the block is released once the trip has fired; SOFT_LIMIT then auto-clears at 10 (A30). S6a: decision → outputs ≤ 1.0 s with pump OFF and vent open; IS3: first PT-101 sample ≥ 20 (t1) → outputs (t3) ≤ 1.0 s. *The true-crossing check (I6b, U-tube film) is not simulated. **S6b:** hold at 10, PT-101 wire broken (0 V); SENSOR_LOSS measured from the last valid reading; *window 0.5 s < t ≤ 0.5 s + one loop period (+1 ms timestamp resolution), vent open ≤ 1.0 s; the trip stays latched (RESET only at the rig). **IS1:** hold at 10, then three blocked-vent ramps at *inflow = rate × C (52 / 104 / 156 std cm³/min = 10 / 20 / 30 mbar/min), each to the trip and back to RUN by auto-clear (three auto-clears stay within the A30 quota); lead = first PT-101 sample ≥ 20 minus first warning sample, ≥ 10 s each (ESP32 timestamps; I6a films the dashboard). **IS2:** hold at 10 for 20 s, then *a +4 mbar gas slug (R07) together with the P6 inflow step 98.7 → 148 std cm³/min; recovery = disturbance → first PT-101 sample back inside 8–12 mbar(g) that *stays inside for 3 s; ≤ 30 s. **C4:** hold at 10; banner "all control on the controller; dashboard is view-only; no Internet"; PASS when a scan of the firmware, dashboard and fake-rig code finds no external address, control has run ≥ *10 s, and the page loaded no resource from outside this PC. *Each scenario starts with a simulated controller restart (fresh Supervisor, NVS zero kept, vessel at 10 mbar(g), or vented for S1); seq keeps counting so one session holds all runs. Each card keeps its last result while other scenarios run. | CONFIRMED (team request); items marked * ASSUMED |
