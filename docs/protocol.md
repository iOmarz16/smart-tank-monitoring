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
 "trip_decision_us":0,"trip_output_us":0}
```

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

### Pong
`{"type":"pong","t_pc":<echoed>,"t_esp":<ESP32 ms, 3 decimals>}`

## Client → ESP32

Only `{"type":"ping","t_pc":<number>}` is accepted. Every other message is ignored.
The dashboard has no control authority.

## Replay

On each (re)connect the whole ring buffer (1500 samples, 150 s) is sent first, flagged
`replayed: true`. The client deduplicates by `seq` (first copy wins), so a Wi-Fi outage
shorter than ~150 s leaves no gap in the log.

## Latency (test I1)

Clock offset from ping–pong round trips, minimum-RTT estimate:
`offset = (t_pc_send + t_pc_recv)/2 − t_esp`, then
`latency = t_arrival_pc − (t_esp_ms + offset)`. 100 pings at connect, 20 every 30 s after.
