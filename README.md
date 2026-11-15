# TEAM M021 — ICS firmware and local dashboard

Pressure control of the 5 L air headspace in a sealed 10 L vessel. All pressures are gauge, **mbar(g)**.
Source of truth: `docs/TEAM_M021_Third_Report.pdf` (git-ignored: it holds names and invoices).
Decisions and assumptions: [`docs/assumptions.md`](docs/assumptions.md). Wire protocol: [`docs/protocol.md`](docs/protocol.md).

| Layer | Acts at | Where |
|---|---|---|
| PI control | 10 mbar(g) | firmware, RUN only (Kc 36, Ti 8 s, PWM 10 Hz on PCV-101) |
| Early warning | 14 mbar(g) or predicted 20 within 12 s | firmware flag, shown on the dashboard |
| Software limit (trip) | 20 mbar(g) | firmware: pump OFF + vent OPEN, **latched** |
| Hard trip | 29 mbar(g) | firmware |
| Sensor loss | no valid sample > 0.5 s, or P < 1 mbar(g) for 30 s in RUN | firmware |
| Fail-safe hardware | power loss / reset / watchdog | 10 kΩ pull-downs → pump OFF, valve OPEN |

The dashboard is **view and log only**. It cannot start, stop or reset anything.

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

Serial commands (type and press Enter):

| Command | Allowed in | Effect |
|---|---|---|
| `STATUS` | any | one-line state, pressure, PI, trip timestamps, free heap |
| `START` | IDLE, sensor valid | RUN: pump ON, PI active |
| `STOP` | RUN, TEST | IDLE: pump OFF, valve OPEN |
| `RESET` | TRIPPED | back to IDLE, only if P < 20 mbar(g) and the sensor is valid |
| `ZERO` | IDLE | stores the mean of the last 1 s as the zero offset (NVS); refused if > ±3 mbar |
| `TEST` | IDLE | test mode, outputs under manual control, **trip limits still active** |
| `TEST PUMP ON` / `OFF` | TEST | pump |
| `TEST VALVE ENERGIZE` / `RELEASE` | TEST | valve closed / open |
| `TEST DUTY <0-100>` | TEST | valve energized (closed) duty %, 10 Hz PWM |
| `TEST EXIT` | TEST | back to IDLE |

In TEST mode the console prints `P_raw`, `P_f`, `V_gpio`, the pump state and the valve state at 5 Hz.
State changes are printed as `EVENT state=... trip_reason=...`.

---

## 3. First power-on and wiring check (TEST mode)

Follow Figure 12 and assembly step 6 of §5.1.1. Lid open or HV-101 open, so the vessel cannot pressurize.

1. **Without the ESP32** (Fig. 12 note 8): adapter 24 V at the switch; Buck 1 = 5.0 V, Buck 2 = 12.0 V
   measured with a multimeter **before** anything is connected to them. Touch each TRIG to 3.3 V:
   the valve clicks, the pump runs. The divider midpoint reads ≈ 0 V.
2. Check the QBM2030-30 range selector is on **3000 Pa** (0–30 mbar). Its OUT goes to the divider only,
   never straight to the ESP32.
3. Flash the ESP32 over USB **before** wiring it into the box. Open the monitor; you should see
   `M021 controller boot`, then `EVENT state=IDLE`.
4. Wire the ESP32 in, power the 24 V supply, and type `STATUS`: `state=IDLE`, `P_raw` near 0, pump 0.
5. `TEST`, then step through:
   - `TEST VALVE ENERGIZE`: valve clicks closed. `TEST VALVE RELEASE`: clicks open.
   - `TEST DUTY 50`: steady 10 Hz clicking. `TEST DUTY 0`.
   - `TEST PUMP ON`: pump runs. `TEST PUMP OFF`.
   - Watch `V_gpio`: ≈ 0 mV at ambient pressure, never above ≈ 2330 mV.
   - `TEST EXIT`.
6. Close the lid and HV-101. `TEST`, `TEST VALVE ENERGIZE`, `TEST PUMP ON`, and watch `P_raw` rise.
   Stop well below 14 (`TEST PUMP OFF`, `TEST VALVE RELEASE`). The pressure must fall back.
7. Power-loss check (Table D layer 5): with the valve energized and the pump on in TEST, pull the
   ESP32 USB/power. The pump stops and the valve vents.

### Zeroing (§5.1.1 step 8, before each test day)
Let the filled rig settle for about 1 h with the headspace vented to atmosphere. In IDLE type `ZERO`.
The offset is saved and survives reboots. Then do the 60 s zero-flow check.

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

### Without hardware
```bat
run_fake_rig.bat --scenario normal          :: or ramp | sensor_loss | wifi_outage
run_dashboard.bat --rig ws://127.0.0.1:8765/
```
`fake_rig.py` runs a Python port of the firmware logic (constants read from `config.h`) on a
simple headspace model. You can type `START` / `STOP` / `RESET` / `ZERO` / `STATUS` in its window.
It is a test tool, not a validated model. Its own Python loop can stretch a few loop periods while
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
trip at 20 mbar(g) is still enforced in TEST and latches. **The trip does not re-close the vent
automatically** (HYSYS T3 does; the firmware latches, by decision Q1). After the vent has brought P
below 20 mbar(g), type `RESET` on the serial console to return to IDLE.

| Test | Procedure | Pass criterion | Tool output |
|---|---|---|---|
| **I1** Spec 4 | RUN for 10 min (≈ 6000 updates) with the dashboard open. Cross-check 20 updates with 240 fps video (see open item O2). | every update ≤ 1.0 s; report median / p95 / max | `I1` |
| **I2** Spec 5 | RUN for 10 min. Mid-run, switch the **laptop** Wi-Fi off for 60 s, then on again. | mean ≤ 100 ms, no period > 110 ms, ≥ 5 samples per 1 s, timestamps increasing, no missing seq | `I2` |
| **I3a** Spec 6 | Vent-closed ramp (above) to the trip. 10 repeats. | t3 − t1 ≤ 1.0 s | extras: `t1 -> output write` (software t3; hardware t3 see O1) |
| **I3b** | Same ramp to 15 mbar(g), then pull the PT-101 OUT wire. | pump OFF and vent OPEN ≤ 1.0 s after the 0.5 s timeout (`trip_reason=SENSOR_LOSS`) | `EVENT` line, `STATUS`, CSV |
| **I3c** | Repeat I3a and I3b with the dashboard closed and the laptop Wi-Fi off. | same, with no network | serial `EVENT` + `STATUS` (`trip_dec_us`, `trip_out_us`) |
| **I4** Constraint 4 | Run the P5 sequence with the laptop on M021-RIG only. Review the code for external hosts: `git grep -nIE "https?://\|ntp\|pool\.\|\.com\|\.org" -- firmware dashboard tools` (expected matches today: `127.0.0.1` in `dashboard/app.py`, and false positives like `re.compile`; none in `firmware/`). | control, warning, trip and logging all work; no external address | — |
| **I5** IS2 | P6: steady at 10 mbar(g) in RUN, step FI-101 98.7 → 148 std cm³/min, five repeats. | logged and recomputed duty differ ≤ 1 % at every sample; 0–100 % without windup | `I5` |
| **I6a** IS1 | 30 mbar/min vent-closed ramp: warning on the dashboard vs the PT-101 crossing of 20 mbar(g). | warning ≥ 10 s before 20 mbar(g) | extras: `warning lead at the dashboard` |
| **I6b** IS3 | Film a water U-tube (204 mm H₂O = 20 mbar(g)) and the vent at 240 fps. | vent open ≤ 1.0 s after the true crossing | video. If it fails, set `LAG_COMP_ENABLED = true` (after approval) |

**P5** (Table G): vent-closed ramp as above, warning at 14, vent open at 20, peak ≤ 22 and never > 29.
Then `RESET` once P < 20, `START` again, and unplug the supply at 15 mbar(g): the valve must vent.

---

## 6. Open items

- **O1** I3a t3 "read by a spare ESP32 input": no input pin is assigned in Figure 12, so it is not in
  the firmware. The CSV has software timestamps (`trip_decision_us`, `trip_output_us`).
- **O2** I1 and I6b mention an LED toggled at t0 and a vent LED. The build has no LED output; the
  DevKit's onboard LED (GPIO2) is not in Figure 12. Not implemented without approval.
- **O3** `PUMP_OFF_AT_SOFT_LIMIT = true` (Spec 6) vs Table D (pump off only at 29): waiting for CHE.
- **O4** `ZERO` refuses offsets above ±3 mbar (assumption A13): confirm.
- Nothing here has run on the real ESP32 yet. See the summary in the last commit.
