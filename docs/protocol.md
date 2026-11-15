# ESP32 ↔ dashboard protocol

Transport: WebSocket, `ws://192.168.4.1:81/` on the ESP32 access point (`M021-RIG`).
`tools/fake_rig.py` serves the identical protocol on `ws://127.0.0.1:8765/`.
No Internet, DNS, NTP or external host is involved (Constraint 4).

## ESP32 → client

### Sample (one per 100 ms control loop)
```json
{"type":"sample","seq":1234,"t_esp_ms":123456,"P_raw":10.0213,"P_f":10.0087,"valid":true,
 "u_open":39.4712,"integ":10.96421,"valve_duty":60.5288,"pump_on":true,"warning":false,
 "t_to_limit":null,"state":"RUN","trip":false,"trip_reason":"NONE","loop_period_us":100004,
 "replayed":false,"slope":0.0021,"v_gpio_mv":775.3,"sensor_fault":false,"zero_offset":0.1500,
 "zero_forced":false,"trip_decision_us":0,"trip_output_us":0,"t3_us":0,"t3_source":"NONE",
 "led_on":false,"rb_valve_off":false,"rb_pump_off":false,"reset_reason":"POWERON",
 "trip_latched":false,"auto_clears":0,"P_corr":10.1987,"P_comp_assumed":14.62,"leak_rate":0.1437}
```

Every sample is stored in the ESP32 ring buffer before it is sent, packed to 48 B (A33, A34). Values
therefore arrive **quantized**: `v_gpio_mv` 0.1 mV · `P_raw`, `P_f` 0.002 mbar (range ±65.5) ·
`slope` 0.001 mbar/s · `t_to_limit` 0.02 s · `u_open`, `valve_duty` 0.01 % · `zero_offset`
0.0002 mbar · `P_comp_assumed` 0.02 mbar (range ±655) · `leak_rate` 0.0001 mbar/s. `P_corr` is rebuilt
from `P_f` (same resolution). `integ`, `t_esp_ms`, `loop_period_us` and all `*_us` trip times are exact. Control and
trips run on the unquantized values.

| Field | Meaning |
|---|---|
| `seq` | strictly increasing from 0 at boot; the dedup key |
| `t_esp_ms` | ESP32 clock (esp_timer, ms since boot) at loop start |
| `P_raw` | mbar(g), zero offset applied; `null` if the read failed |
| `P_f` | EMA-filtered pressure (held while invalid); `null` before the first valid sample |
| `valid` | sample passed range + drop checks |
| `u_open` | vent opening % (PI output in RUN; 100 in IDLE/TRIPPED) |
| `integ` | PI integrator Σe (mbar·samples) **after** this loop's update |
| `valve_duty` | PCV-101 energized (closed) duty %; RUN: `100 − u_open` |
| `t_to_limit` | s to 20 mbar(g); `null` when slope ≤ `MIN_WARNING_SLOPE` |
| `state` | `BOOT` · `IDLE` · `RUN` · `TEST` · `TRIPPED` |
| `trip_reason` | `NONE` · `SOFT_LIMIT` · `SENSOR_LOSS` · `HARD_LIMIT` |
| `loop_period_us` | micros() between this loop start and the previous one |
| `replayed` | sent from the buffer (after a connect, or ≥ 3 samples behind): exclude from latency |
| `slope` | 5-sample regression of `P_f`, mbar/s (`null` until 5 valid samples) |
| `sensor_fault` | invalid sample but not yet stale (< 0.5 s): PI frozen, duty held |
| `trip_decision_us`, `trip_output_us` | esp_timer µs of the trip decision and of the safe-output write (0 = none) |
| `t3_us`, `t3_source` | I3a t3: `READBACK` = OFF edge on GPIO32/33 (read-back enabled), `SOFTWARE` = `trip_output_us`, `READBACK_TIMEOUT` = no edge within 1 s, `NONE` = not tripped. With read-back the value appears a sample or two after the trip sample |
| `zero_forced` | the active zero offset was set with `ZERO FORCE` (beyond ±3 mbar) |
| `led_on` | GPIO2 LED level written in this loop (`LED MODE SAMPLE`: toggles each sample; `VENT`: vent commanded fully open) |
| `rb_valve_off`, `rb_pump_off` | read-back levels (true = MOSFET OFF); always false when read-back is disabled |
| `reset_reason` | why the ESP32 last booted (`esp_reset_reason()`), the same in every sample since boot: `POWERON` · `EXT` · `SW` · `PANIC` · `INT_WDT` · `TASK_WDT` · `WDT` · `DEEPSLEEP` · `BROWNOUT` · `SDIO` · `UNKNOWN`. After a watchdog, panic or brownout reset, any latched trip was lost |
| `v_gpio_mv` | raw averaged pin voltage at GPIO34 (mean of 32 reads), mV, before any zero offset. `P_raw = v_gpio_mv × 0.015 − zero_offset` (÷5 module, A29). The open-circuit check compares it with `OPEN_CIRCUIT_MV_THRESHOLD` |
| `trip_latched` | `true` while TRIPPED and only a serial `RESET` can clear the trip. `false` in a SOFT_LIMIT trip that will auto-clear at `P_raw` ≤ 10 (A30), and whenever not tripped |
| `auto_clears` | SOFT_LIMIT auto-clears in the last 300 s (0–3). An auto-clear shows as `state` TRIPPED → RUN |
| `P_corr` | **Leak model, display/log only (A34).** mbar(g): `P_f` + 0.19 while the pump ran during the reading (previous loop's pump command); `null` before the first valid sample |
| `P_comp_assumed` | **ASSUMPTION, not a measurement (A34).** mbar(g): `P_corr` + all pressure assumed lost to the leak since the vessel was last empty (accumulated with the vent energized, weighted by its closed duty; reset with the pump OFF and `P_corr` < 0.05). Grows without bound while the pump runs with the vent closed. Never used for trips, warning, PI, plausibility or the dashboard status, and not evidence for any specification |
| `leak_rate` | Leak model (A34), mbar/s: `k·√P_corr`, k = 0.045 (pump ON) or 0.28 (pump OFF) mbar^0.5/s; 0 at `P_corr` ≤ 0 |

### Pong
`{"type":"pong","t_pc":<echoed>,"t_esp":<ESP32 ms, 3 decimals>}`

## Client → ESP32

Only `{"type":"ping","t_pc":<number>}` is accepted. Every other message is ignored.
The dashboard has no control authority.

## Replay

On each (re)connect the whole ring buffer (1500 samples, 150 s) is replayed, flagged
`replayed: true`. **Live samples are always sent first**: each pass of the network task sends every
new live sample, then at most a token-bucket share of the backlog (`REPLAY_RATE_HZ` = 100 samples/s,
burst ≤ 2). A live sample therefore never waits behind the backlog; a full backlog drains in ~17 s.
Samples arrive out of `seq` order during that time. The client deduplicates by `seq` (first copy
wins), so a Wi-Fi outage shorter than ~150 s leaves no gap in the log.

## Latency (test I1)

Clock offset from ping–pong round trips, minimum-RTT estimate:
`offset = (t_pc_send + t_pc_recv)/2 − t_esp`, then
`latency = t_arrival_pc − (t_esp_ms + offset)`. 100 pings at connect, 20 every 30 s after.
