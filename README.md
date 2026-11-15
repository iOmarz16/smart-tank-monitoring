# TEAM M021 — ICS firmware and local dashboard

Pressure control of the 5 L air headspace in a sealed 10 L vessel. All pressures are gauge, **mbar(g)**.
Source of truth: `docs/TEAM_M021_Third_Report.pdf` (git-ignored: it holds names and invoices).
Decisions and assumptions: [`docs/assumptions.md`](docs/assumptions.md). Wire protocol: [`docs/protocol.md`](docs/protocol.md).

| Layer | Acts at | Where |
|---|---|---|
| PI control | 10 mbar(g) | firmware, RUN only (Kc 36, Ti 8 s, PWM 10 Hz on PCV-101) |
| Early warning | 14 mbar(g) or predicted 20 within 12 s | firmware flag, shown on the dashboard |
| Software limit (trip) | 20 mbar(g) | firmware: pump OFF (CHE-confirmed) + vent OPEN. A trip that started in **RUN** auto-clears at ≤ 10 mbar(g) and RUN resumes; after 3 auto-clears within 5 min the next one **latches**. A trip that started in TEST always latches |
| Hard trip | 29 mbar(g) | firmware, **latched** |
| Sensor loss | no valid sample > 0.5 s, or V_gpio < `OPEN_CIRCUIT_MV_THRESHOLD` for 30 s while the pump is ON (any state, TEST too) | firmware, **latched** |
| Fail-safe hardware | power loss / reset / watchdog | 10 kΩ pull-downs → pump OFF, valve OPEN; the task watchdog also drives both LOW before its reboot |
| Inherent limit | 440 mbar | pump deadhead (vessel safe). PT-101 overload is 100 mbar (sensor at risk) |

There is **no passive relief** on the prototype: no PSV-101 water seal, and the vessel's factory
safety valve was removed (Table C, §5.1.1). Nothing above the software trips vents the vessel by
itself; operation is attended at all times.

The dashboard is **view and log only**. It cannot start, stop or reset anything.

**As built, PT-101 input** (overrides Figure 12): a "Voltage Sensor" module that divides by 5
(no 33k/10k, no 100 nF) feeds GPIO34. `P_raw = V_gpio[mV] × 0.015 − zero_offset`
(`SENSOR_DIVIDER_RATIO = 5.0`); 0–10 V from the sensor is 0–2.0 V at the pin. Each sample averages
32 ADC reads (`ADC_AVERAGE_READS`); `STATUS` shows how long they take (`adc_us`, `adc_us_max`).

```
firmware/        PlatformIO project (ESP32, Arduino core 2.0.17); constants in include/config.h
dashboard/       app.py (backend) + static/ (UI, all local) ; logs/ (session CSVs, git-ignored)
tools/           fake_rig.py (simulator, same protocol) ; analyze_log.py (I1, I2, I5 + extras)
docs/            assumptions.md, protocol.md, the report (local only)
run_dashboard.bat, run_fake_rig.bat
```

---

## 1. Windows setup (do this while you still have Internet)

### 1.1 CH340 USB driver
1. Plug the ESP32 in. If Device Manager shows **USB-SERIAL CH340 (COMx)** under *Ports*, skip this.
2. Otherwise install the CH341SER driver from the chip maker (WCH), re-plug, and note the COM port.

### 1.2 PlatformIO CLI
PlatformIO is installed in its own virtual environment (`%USERPROFILE%\.platformio\penv`), the
same place the official installer uses. Nothing goes into the global Python.
```bat
py -3.13 -m venv %USERPROFILE%\.platformio\penv
%USERPROFILE%\.platformio\penv\Scripts\python -m pip install platformio
```
Add an alias for this shell: `doskey pio=%USERPROFILE%\.platformio\penv\Scripts\pio.exe $*`

The first `pio run` downloads the pinned ESP32 toolchain (already done on the team laptop).

### 1.3 Python dependencies (dashboard + tools)
```bat
py -3.13 -m venv dashboard\.venv
dashboard\.venv\Scripts\python -m pip install -r requirements.txt
```
The only package is `websockets`. The UI uses no CDN, web fonts or external chart library.

> On this laptop `C:\Python313` prints *"Could not find platform independent libraries"*. It is
> harmless: the install is split between `C:\Python313` and `%LOCALAPPDATA%\Programs\Python\Python313`
> (details in `docs/assumptions.md`, section D). A Python 3.13 *Repair* from the installer fixes it.

---

## 2. Build and flash

```bat
cd firmware
pio run                              :: compile
pio run -t upload --upload-port COM5 :: flash (use your COM port)
pio device monitor -p COM5           :: serial console, 115200 baud
```
Before flashing for the first time, edit `firmware/include/config.h`:
`WIFI_AP_SSID` (default `M021-RIG`) and **`WIFI_AP_PASSWORD`** (default `CHANGE_ME`, change it).
Do not change pins, thresholds, gains or timings without team approval.

- The build uses its own partition table, `firmware/partitions_nocoredump.csv` (the Arduino
  default without the coredump partition, so a crash reboots at once). The upload writes it; the
  NVS area is at the same address, so a saved zero survives a re-flash.
- `platformio.ini` sets `monitor_dtr = 0` / `monitor_rts = 0`, so opening `pio device monitor`
  does not reset the board (verified on the real DevKit). Other serial programs may still reset it.
- At boot the console prints `HEAP ...` lines: free heap, largest free block and minimum free
  heap, before and after the ring buffer, before Wi-Fi and after Wi-Fi + WebSocket. The ring holds
  1500 samples packed at 48 B each (72 KB), in 10 blocks. If less than 40 KB is free after Wi-Fi, a
  `WARNING` line follows: report the HEAP lines and do not run tests. `FATAL ring buffer
  allocation failed` still stops the controller with the outputs safe.
- At boot the console prints `reset_reason=...`. After `TASK_WDT`, `INT_WDT`, `PANIC`, `WDT` or
  `BROWNOUT` it adds a warning: a latched trip was lost with the reset. Every sample also carries
  `reset_reason`.

### Bring-up firmware (wiring checks only)
A separate environment, `bringup` (`firmware/bringup/bringup.cpp`), for the first hardware test.
It is **not** the controller: no PI, no Wi-Fi, no NVS, no sensor-loss detection (a broken PT-101
wire reads ~0 mbar), so stay at the rig. GPIO26/27 go LOW first in `setup()`; it prints
`v_gpio_mv`, `P_mbar` (= mV × 0.015, no zero offset), pump and valve every 200 ms, and checks
P ≥ 20 mbar → pump OFF + valve released + `TRIP` every 50 ms.
```bat
cd firmware
pio run -e bringup -t upload --upload-port COM5
pio device monitor -e bringup -p COM5
```
Commands: `p<N>` pump ON for N s (1–30), `v1` valve energize (closed), `v0` release (open),
`s` stop (pump OFF, valve released), `r` clear a TRIP (only if P < 20). Flash the controller
again afterwards with `pio run -t upload --upload-port COM5`.

Serial commands (type and press Enter):

| Command | Allowed in | Effect |
|---|---|---|
| `STATUS` | any | one-line state, pressure, V_gpio, PI, zero (`zero_ok`), IDLE time, trip timestamps; then free heap and free stack per task |
| `START` | IDLE, sensor valid, **valid ZERO saved** | RUN: pump ON, PI active. Refused after a first flash, after `ZERO FORCE`, or if the zero could not be saved |
| `STOP` | RUN, TEST | IDLE: pump OFF, valve OPEN |
| `RESET` | TRIPPED | back to IDLE, only if P < 20 mbar(g) and the sensor is valid. Needed for every latched trip. On a SOFT_LIMIT trip that would auto-clear, `RESET` goes to IDLE instead (the pump stays OFF) and starts a fresh auto-clear count |
| `ZERO` | IDLE ≥ 60 s | stores the mean of the last 1 s of IDLE samples as the zero offset (NVS). Refused if the window is not steady (max − min > 0.3 mbar) or the offset is > ±3 mbar |
| `ZERO FORCE` | IDLE ≥ 60 s | same, but accepts an offset beyond ±3 mbar, **up to ±5 mbar** (absolute cap). Prints a `WARN` line; `zero_forced=true` is then logged in every sample (and shown on the dashboard) until a normal `ZERO`. **`START` stays refused** with a forced zero |
| `LED MODE SAMPLE` | any | GPIO2 LED toggles at t0 of every sample (I1 video) |
| `LED MODE VENT` | any | GPIO2 LED ON while the vent is commanded fully open (I6b video). **Default at boot** |
| `TEST` | IDLE | test mode, outputs under manual control, **trip limits still active** |
| `TEST PUMP ON [<s>]` / `OFF` | TEST | pump ON for `<s>` seconds (default 60, **max 300**; more is refused), then **auto-OFF** (`EVENT TEST pump OFF`). A new `TEST PUMP ON` replaces the remaining time. All trips and the open-circuit check stay enforced |
| `TEST VALVE ENERGIZE` / `RELEASE` | TEST | valve closed / open |
| `TEST DUTY <0-100>` | TEST | valve energized (closed) duty %, 10 Hz PWM |
| `TEST EXIT` | TEST | back to IDLE |

In TEST mode the console prints `P_raw`, `P_f`, `V_gpio`, the pump state and the valve state at 5 Hz.
State changes are printed as `EVENT state=... trip_reason=...`; a trip line says either
`LATCHED (RESET required)` or `auto-clears at P_raw <= 10.0`. Every auto-clear is logged as
`EVENT AUTO-CLEAR SOFT_LIMIT -> RUN (bumpless PI restart) ... auto_clears=n/3`, and every sample
carries `trip_latched` and `auto_clears` (CSV and dashboard).

**SOFT_LIMIT auto-clear** (team decision, `docs/assumptions.md` A30). Only for a SOFT_LIMIT trip
that started in RUN: once P_raw ≤ 10.0 mbar(g) with a valid, non-stale sample, the rig returns to
RUN by itself. The pump restarts and the PI starts bumpless, from the fully open vent. Up to 3
auto-clears are allowed within 5 min; the next SOFT_LIMIT trip in that window latches. The
trip also latches, with its reason changed, if during it the pressure reaches 29
(`HARD_LIMIT`), the sensor goes stale, or a reading drops by more than 2 mbar in one sample
(`SENSOR_LOSS`, e.g. a wire break). To keep the pump from restarting, type `RESET` while
P < 20: the rig goes to IDLE.

While the pump is ON, in any state, the open-circuit check is armed: if V_gpio stays below
`OPEN_CIRCUIT_MV_THRESHOLD` for 30 s, the rig trips `SENSOR_LOSS`. This also happens with a
healthy sensor when the vessel cannot pressurize (lid or HV-101 open, FI-101 closed, vent released
with low inflow), so keep pump-on checks on an open vessel short.

If a command gets no reply within 1 s the console says so; the command may still run, and its
reply is then printed as `LATE reply ...`. Check with `STATUS`.

---

## 3. First power-on and wiring check (TEST mode)

Follow Figure 12 and assembly step 6 of §5.1.1. Lid open or HV-101 open, so the vessel cannot pressurize.

1. **Without the ESP32** (Fig. 12 note 8): adapter 24 V at the switch; Buck 1 = 5.0 V, Buck 2 = 12.0 V
   measured with a multimeter **before** anything is connected to them. Touch each TRIG to 3.3 V:
   the valve clicks, the pump runs. The voltage-sensor module output reads ≈ 0 V.
2. Check the QBM2030-30 range selector is on **3000 Pa** (0–30 mbar). Its OUT goes to the ÷5 module
   input only, never straight to the ESP32 (10 V would destroy GPIO34).
3. Flash the ESP32 over USB **before** wiring it into the box. Open the monitor; you should see
   `M021 controller boot`, `reset_reason=...`, a `zero_offset=0.000 mbar  START refused until a
   valid ZERO is saved` line (first flash: nothing in NVS yet), then `EVENT state=IDLE`.
4. Wire the ESP32 in, power the 24 V supply, and type `STATUS`: `state=IDLE`, `P_raw` near 0, pump 0.
5. `TEST`, then step through:
   - `TEST VALVE ENERGIZE`: valve clicks closed. `TEST VALVE RELEASE`: clicks open.
   - `TEST DUTY 50`: steady 10 Hz clicking. `TEST DUTY 0`.
   - `TEST PUMP ON`: pump runs. `TEST PUMP OFF` within a few seconds (the vessel is open, so a
     pump left ON trips `SENSOR_LOSS` after 30 s by the open-circuit check; `RESET` if it does).
   - Watch `V_gpio`: ≈ 0 mV at ambient pressure (the ADC floor reads ~75–150 mV), never above
     ≈ 2000 mV (10 V / 5; the 29 mbar(g) hard trip is at 1933 mV).
   - `TEST EXIT`.
6. Close the lid and HV-101. `TEST`, `TEST VALVE ENERGIZE`, `TEST PUMP ON`, and watch `P_raw` rise.
   Stop well below 14 (`TEST PUMP OFF`, `TEST VALVE RELEASE`). The pressure must fall back.
7. Power-loss check (Table D layer 5): with the valve energized and the pump on in TEST, pull the
   ESP32 USB/power. The pump stops and the valve vents.

### Zeroing (§5.1.1 step 8, before each test day)
Let the filled rig settle for about 1 h with the headspace vented to atmosphere. In IDLE (at least
60 s in IDLE, so any blow-down is over) type `ZERO`. The console answers `OK ZERO offset=...` and
then `OK zero ... saved to NVS`; only then is `START` allowed. The offset survives reboots. Then do
the 60 s zero-flow check.

Why it is required: a 0 V input reads about 75–150 mV on the ESP32 ADC (its floor), i.e.
+1.1–2.3 mbar(g) with the ÷5 module. The zero removes that floor. A zero taken while the vessel
still holds pressure would shift **every** limit up by the same amount, so `ZERO` is refused unless
the last 1 s of IDLE samples is steady (max − min ≤ 0.3 mbar, i.e. 20 mV at the pin). The module
has no 100 nF filter. If `ZERO` keeps failing on the spread with a vented vessel, the reading is
too noisy: report the spread to the team, and do not raise `ZERO_MAX_SPREAD` on your own.

If `ZERO` is refused (offset beyond ±3 mbar), first check that the vessel really is vented and that
the range selector is on 3000 Pa. Use `ZERO FORCE` only once you know why the offset is large; it
is capped at ±5 mbar, and `START` stays refused until a normal `ZERO` succeeds. If the console
prints `WARN could not save the zero offset to NVS`, `START` is refused too: try `ZERO` again.

The zero corrects the **offset** only, not the gain of the ADC/divider chain. Do the two-point
U-tube check of P0 (Table G: 102 mm H₂O = 10 mbar, 204 mm = 20 mbar) before trusting the 20 and
29 mbar(g) limits.

### 3.1 Optional: output read-back for I3a (GPIO32 / GPIO33)

Off by default (`READBACK_ENABLED = false` in `config.h`). When it is off, t3 is the software
timestamp taken right after the outputs are written (`t3_source = SOFTWARE`).

**Never wire a MOSFET output directly to the ESP32.** The valve output switches 12 V and the pump
output switches 5 V; the ESP32 inputs tolerate 3.3 V at most.

1. **Check the module polarity first** (power on, ESP32 not connected, TRIG touched to 3.3 V and
   released). The 400 W dual-MOSFET trigger modules are expected to switch **low-side**:
   - `OUT+` stays at the supply voltage all the time (12 V or 5 V), whether TRIG is high or low;
   - `OUT−` reads ≈ 0 V while TRIG is high (load ON) and rises to the supply through the load when
     TRIG is low (load OFF).

   If you measure that instead, the logic below holds. If `OUT−` does **not** behave like this, stop:
   the read-back logic would be inverted and must be changed in `readback.cpp`.
2. Take the read-back from **`OUT−`** (measured against the common GND) through a divider:

   | Line | From | Divider (top / bottom to GND) | At the pin |
   |---|---|---|---|
   | valve | MOSFET 1 `OUT−` (12 V) | **36 kΩ / 10 kΩ** → GPIO32 | 2.61 V at 12.0 V (2.87 V at 13.2 V) |
   | pump | MOSFET 2 `OUT−` (5 V) | **10 kΩ / 12 kΩ** → GPIO33 | 2.73 V at 5.0 V |

   Both stay below 3.0 V and above the ESP32's 2.475 V minimum HIGH level (0.75 × 3.3 V) with
   Buck 2 set to 12.0 V and Buck 1 to 5.0 V. The 36k/10k divider is only valid for a valve rail of
   about 11.4–13.8 V; keep Buck 2 at 12.0 V as Figure 12 requires. Check with a multimeter at the
   GPIO side of each divider, with the ESP32 **disconnected**, before connecting it:
   ≤ 3.0 V with the output OFF, ≈ 0 V with it ON.
3. Logic: **input HIGH = output OFF** (valve de-energized = vent open; pump stopped). A rising edge
   is the output turning off; the firmware timestamps it in an interrupt.
4. Set `READBACK_ENABLED = true`, rebuild and flash. At boot the console prints `readback=ENABLED`.
   The samples then carry `rb_valve_off` / `rb_pump_off`, and after a trip `t3_us` is the latest
   OFF edge of the outputs that were ON at the decision (`t3_source = READBACK`).
   `READBACK_TIMEOUT` means no edge came within 1 s: check the wiring.

GPIO32/33 are input-capable ADC1 pins with no boot strapping function, so they are safe to use.

### 3.2 GPIO2 LED (I1 / I6b video)
The DevKit's onboard LED is on GPIO2, a strapping pin that must be LOW at reset. The board's LED and
resistor to GND keep it LOW, and the firmware drives it LOW first thing in `setup()`. **Do not
connect anything that pulls GPIO2 high**; otherwise the board cannot enter flashing mode.

### 3.3 Bench open-circuit measurement (sets `OPEN_CIRCUIT_MV_THRESHOLD`)

`OPEN_CIRCUIT_MV_THRESHOLD` in `config.h` is **180 mV, measured** (reported 2026-11-16: 0 V at the
sensor input reads a steady 142.0 mV, max 147.7 mV; A25). Repeat this measurement if the board, the
÷5 module or the wiring changes, and once more for step 6 after the 1 MΩ pull-down is fitted.
Procedure:

1. Vessel vented (lid open or HV-101 open). IDLE. Start the dashboard so every sample, including
   `v_gpio_mv` and `P_raw`, is logged to the CSV; Wi-Fi active, since radio noise is part of the
   reading.
2. `TEST` (pump OFF, valve released).
3. Unplug the PT-101 **OUT** wire at the ÷5 module input. Leave the module connected to GPIO34:
   its bottom resistor is what pulls the pin to 0 V, exactly as in a real break of the sensor wire.
4. Record V_gpio and P_raw for 60 s (5 Hz `TEST ...` lines and the CSV). The state may change to
   `TRIPPED` / `SENSOR_LOSS` (if P_raw falls below −2 mbar(g), or through the drop check). That is
   expected; `v_gpio_mv` is still logged in every sample, so keep recording (use `STATUS` for spot
   values once the TEST lines stop).
5. Plug the wire back in. `RESET` if tripped (needs a valid sample), `TEST EXIT` otherwise.
6. **Second case: module-to-ESP32 wire** (with the **1 MΩ pull-down GPIO34 → GND** fitted, team
   decision A32). Repeat steps 2–5, this time unplugging the wire from the module output to
   **GPIO34**. With the pull-down the pin must read like step 4 (near the ADC floor); without it,
   GPIO34 floats and can read anything. Include these readings in the maximum of step 7. If it
   does not stay below the threshold, stop and report it. The pull-down makes the reading
   ~0.6 % low: redo the two-point U-tube check (P0) after fitting it.
7. Threshold = **measured max V_gpio (step 4) + margin** (team decision, e.g. 50 mV). It must stay
   **≤ about 300 mV**: a healthy rig on the slowest ramp (10 mbar/min from 0) must cross it well
   inside the 30 s window (with the ÷5 module, 5 mbar(g) = 333 mV at the pin; 300 mV is crossed
   at 27 s). If max + margin is above that, stop and discuss before flashing.
8. Set `OPEN_CIRCUIT_MV_THRESHOLD`, rebuild, flash, and record the measured values and the chosen
   threshold in `docs/assumptions.md` (A25).

---

## 4. Dashboard

1. Join the laptop to Wi-Fi **M021-RIG** (it has no Internet; Windows will say so). Turn off
   auto-connect for other networks, so Windows does not leave the rig network.
2. Run `run_dashboard.bat`. The browser opens on `http://127.0.0.1:8000/`.
3. Every session writes `dashboard/logs/session_<date>_<time>.csv` (all sample fields +
   `t_arrival_pc_ms`, `t_arrival_wall`, `clock_offset_ms`, `latency_ms`, deduplicated by `seq`).

The status panel shows **Normal** (green), **Warning** (yellow) or **Trip** (red, with the reason).
It turns grey "NO DATA" when samples stop for more than 3 s. Latency (median / p95 / max) excludes
replayed samples. The clock offset comes from 100 ping round trips at connect (minimum RTT) and is
refreshed every 30 s.

After a Wi-Fi reconnect the ESP32 sends live samples first and replays the backlog interleaved at
100 samples/s (`REPLAY_RATE_HZ`), so the latency of live updates is not affected by the replay; a
150 s backlog drains in about 17 s. "Missing seq" can be above 0 during that time and must return to 0.

The **LED sync** tile is a black/white square that copies the GPIO2 LED state of the newest live
sample at the moment it arrives. Film it together with the LED for the I1 cross-check.

### Without hardware
```bat
run_fake_rig.bat --scenario normal          :: or ramp | ramp_repeat | sensor_loss | wifi_outage | open_circuit
run_dashboard.bat --rig ws://127.0.0.1:8765/
```
`fake_rig.py` runs a Python port of the firmware logic (constants read from `config.h`) on a
simple headspace model. You can type `START` / `STOP` / `RESET` / `ZERO [FORCE]` / `STATUS` /
`LED MODE SAMPLE|VENT` in its window (TEST mode is not simulated). Its simulated NVS starts with a
valid zero of 0.000 mbar; `--fresh-nvs` starts like a first flash (`START` refused until `ZERO`,
which needs 60 s in IDLE). `--no-autostart` stays in IDLE; `--zero-error 3.6` simulates a large
sensor offset to try `ZERO FORCE`; `--adc-floor-mv 120` models the ESP32 ADC floor.
`--scenario open_circuit --adc-floor-mv 120` shows the wire-broken-at-start case: the reading
looks vented while the true pressure rises, until the open-circuit check trips after 30 s.
`ramp` trips SOFT_LIMIT in RUN and auto-clears back to RUN at 10; `ramp_repeat` blocks the vent
again after every auto-clear: three auto-clears, then the fourth trip latches.
It is a test tool, not a validated model.
`python tools/check_rules.py` (e.g. `dashboard\.venv\Scripts\python tools\check_rules.py`) runs the
regression checks of the safety rules (START gate, ZERO rules, open-circuit trip, ÷5 scaling,
SOFT_LIMIT auto-clear and latching). They test the simulator's copy of the logic in `fake_rig.py`,
**not the C++ firmware**. The fake rig's own Python loop can stretch a few loop periods while
it replays after a reconnect, so `analyze_log.py` may report I2 FAIL on fake-rig logs; that says
nothing about the ESP32.

---

## 5. Rig tests I1–I6 (Table I)

Analyze any session log with:
```bat
dashboard\.venv\Scripts\python tools\analyze_log.py dashboard\logs\session_....csv
```
Use `--from-seq` / `--to-seq` to cut one run out of a longer log. The ring buffer holds 150 s, so
if the dashboard was closed during a test, reconnect within 150 s to recover the samples.

**Vent-closed ramp (used by P5, I3a, I6a).** In RUN the PI vents, so the vent-closed ramp of T3/P5 is
run in TEST mode: `TEST`, `TEST VALVE ENERGIZE`, `TEST PUMP ON`, FI-101 set to 148 std cm³/min. The
trip at 20 mbar(g) is still enforced in TEST. **A trip that started in TEST always latches** (team
decision, A30): nothing restarts the pump after a manual test. After the vent has brought P below
20 mbar(g), type `RESET` on the serial console to return to IDLE.
The TEST pump turns OFF after the requested time (`TEST PUMP ON` = 60 s): at 148 std cm³/min
(≈ 28 mbar/min) the trip comes after about 42 s, inside the default. For slower ramps give a
duration: P2 at 10 mbar/min needs ~120 s to 20 mbar(g), so use `TEST PUMP ON 180` (max 300).

| Test | Procedure | Pass criterion | Tool output |
|---|---|---|---|
| **I1** Spec 4 | RUN for 10 min (≈ 6000 updates) with the dashboard open. For the cross-check, `LED MODE SAMPLE`, then film the ESP32 LED (t0) and the dashboard **LED sync** square (t4) in one 240 fps shot; for 20 transitions, t4 − t0 = frames between the LED change and the square change ÷ 240. Set `LED MODE VENT` again afterwards. | every update ≤ 1.0 s; report median / p95 / max | `I1` |
| **I2** Spec 5 | RUN for 10 min. Mid-run, switch the **laptop** Wi-Fi off for 60 s, then on again. | mean ≤ 100 ms, no period > 110 ms, ≥ 5 samples per 1 s, timestamps increasing, no missing seq | `I2` |
| **I3a** Spec 6 | Vent-closed ramp (above) to the trip. 10 repeats. For hardware t3, wire and enable the read-back (section 3.1). | t3 − t1 ≤ 1.0 s | extras: `t3 - t1 (..., READBACK)` or `SOFTWARE` when read-back is off |
| **I3b** | Same ramp to 15 mbar(g), then pull the PT-101 OUT wire. | pump OFF and vent OPEN ≤ 1.0 s after the 0.5 s timeout (`trip_reason=SENSOR_LOSS`) | `EVENT` line, `STATUS`, CSV |
| **I3c** | Repeat I3a and I3b with the dashboard closed and the laptop Wi-Fi off. | same, with no network | serial `EVENT` + `STATUS` (`trip_dec_us`, `trip_out_us`); or reconnect the dashboard within 150 s and the samples (with `t3_us`) are replayed |
| **I4** Constraint 4 | Run the P5 sequence with the laptop on M021-RIG only. Review the code for external hosts: `git grep -nIE "https?://\|ntp\|pool\.\|\.com\|\.org" -- firmware dashboard tools` (expected matches today: `127.0.0.1` in `dashboard/app.py`, and false positives like `re.compile`; none in `firmware/`). | control, warning, trip and logging all work; no external address | — |
| **I5** IS2 | P6: steady at 10 mbar(g) in RUN, step FI-101 98.7 → 148 std cm³/min, five repeats. | logged and recomputed duty differ ≤ 1 % at every sample; 0–100 % without windup | `I5` |
| **I6a** IS1 | 30 mbar/min vent-closed ramp: warning on the dashboard vs the PT-101 crossing of 20 mbar(g). | warning ≥ 10 s before 20 mbar(g) | extras: `warning lead at the dashboard` |
| **I6b** IS3 | `LED MODE VENT` (default). Vent-closed ramp; film the water U-tube (204 mm H₂O = 20 mbar(g)) and the GPIO2 LED, which lights when the vent is commanded open, at 240 fps. | vent open ≤ 1.0 s after the true crossing | video. If it fails, set `LAG_COMP_ENABLED = true` (after approval) |

**P5** (Table G):
1. Vent-closed ramp in TEST as above: warning at 14, vent open at 20, peak ≤ 22 and never > 29. The
   EVENT line must say `LATCHED (RESET required)` (a TEST trip). Wait until P is below 20, then `RESET`.
2. `START`, and unplug the supply at 15 mbar(g): the valve must vent.
3. Auto-clear (A30): this needs a SOFT_LIMIT trip **in RUN**, and in RUN the PI with the open vent
   (≈ 250 std cm³/min at 10 mbar(g)) out-vents the pump, so the rig does not normally get there.
   Do not block or restrict the vent to force it until the team has agreed on a method. Until
   then, the auto-clear and its limit of 3 per 5 min are covered by `tools/check_rules.py` and the
   fake-rig `ramp` / `ramp_repeat` scenarios, which test the Python port only.
   If a RUN trip does happen on the rig, check the CSV: `trip_latched=false` during the trip,
   then `EVENT AUTO-CLEAR` at P_raw ≤ 10.0, `u_open` = 100 on the clear sample, then falling smoothly.

---

## 6. Open items

- ~~O3~~ resolved: CHE confirmed pump OFF at 20 mbar(g) (`PUMP_OFF_AT_SOFT_LIMIT = true`).
- `OPEN_CIRCUIT_MV_THRESHOLD` is provisional until the bench measurement of section 3.3 (now ≤ ~300 mV).
- **1 MΩ pull-down GPIO34 → GND** (decided, A32): fit it, verify it with 3.3 step 6, then redo the
  two-point U-tube check (~0.6 % lower reading).
- Heap after Wi-Fi: read the boot `HEAP` lines (≥ 40 KB must remain, A33).
- ADC read time for 32 reads: measure it at bring-up with `STATUS` (`adc_us`, `adc_us_max`).
- Reading noise without the 100 nF: check the `ZERO` spread (≤ 0.3 mbar) on the real rig.
- P5 step 3: no agreed way yet to trip in RUN on the rig (see P5).
- **P1 is blocked pending CHE**: it holds 25 mbar(g), above the 20 mbar(g) trip that is enforced in
  every state, and there is no passive relief. The firmware has no mode that raises or bypasses the
  limit; CHE is asked to run the leak test below 20 mbar(g) (`docs/assumptions.md`, E).
- ~~m5~~ resolved: opening the serial monitor did not reset the DevKit.
- The MOSFET modules' low-side switching (section 3.1, step 1) must be confirmed with a multimeter
  before the read-back is wired.
- Nothing here has run on the real ESP32 yet.
