// TEAM M021 - all firmware constants.
// Source: TEAM_M021_Third_Report.pdf (Ch. 5 + Table B + Fig. 12 take precedence over Ch. 4).
// Do NOT change pins, thresholds, gains or timings without the team's approval.
// Every assumption is recorded in docs/assumptions.md.
#pragma once

#include <stdint.h>

// ---------------------------------------------------------------------------
// Pins (Figure 12)
// ---------------------------------------------------------------------------
// PT-101 signal via 33k/10k divider + 100 nF. GPIO34 is ADC1: works with Wi-Fi on.
constexpr int PIN_PT101 = 34;
// PCV-101 vent solenoid, normally open, MOSFET 1 TRIG. HIGH = energized = valve CLOSED.
constexpr int PIN_VALVE = 26;
// P-101 pump, MOSFET 2 TRIG. HIGH = pump ON.
constexpr int PIN_PUMP = 27;
// Both TRIG lines have 10 kOhm pull-downs: reset / power loss -> pump OFF, valve OPEN.

// Onboard LED (DevKit, GPIO2) for the 240 fps video in I1 / I6b (user-approved).
// GPIO2 is a strapping pin: it must be LOW/floating at reset. The DevKit LED + resistor
// to GND keeps it LOW; never add a pull-up. Firmware drives it LOW first thing in setup().
constexpr int PIN_LED = 2;
enum class LedMode : uint8_t { VENT = 0, SAMPLE = 1 };
// VENT  : LED ON while PCV-101 is commanded fully open (de-energized), for I6b.
// SAMPLE: LED toggles at t0 (loop start) of every sample, for I1.
constexpr LedMode LED_MODE_DEFAULT = LedMode::VENT;

// Optional MOSFET output read-back for test I3a (t3). Disabled by default.
// The modules switch LOW-SIDE: OUT+ is always at the supply, OUT- goes to ~0 V when the
// MOSFET conducts. Read OUT- through a divider (README section 3.1), NEVER directly:
//   valve OUT- (12 V): 36k / 10k -> 2.61 V at 12.0 V
//   pump  OUT- (5 V) : 10k / 12k -> 2.73 V at 5.0 V
// Logic is inverted: input HIGH = output OFF (valve de-energized/open, pump stopped).
constexpr bool READBACK_ENABLED = false;
constexpr int PIN_RB_VALVE = 32;
constexpr int PIN_RB_PUMP = 33;
constexpr uint32_t READBACK_TIMEOUT_MS = 1000;  // no edge within 1 s of the trip -> t3 not seen

// ---------------------------------------------------------------------------
// PT-101 scaling (Section 5.1, Siemens QBM2030-30, range jumper on 3000 Pa)
// ---------------------------------------------------------------------------
constexpr float DIVIDER_RATIO = 4.3f;     // (33k + 10k) / 10k
constexpr float SENSOR_MBAR_PER_V = 3.0f; // 0-10 V = 0-30 mbar(g)
constexpr int ADC_AVERAGE_READS = 8;      // analogReadMilliVolts reads averaged per loop

// ---------------------------------------------------------------------------
// Timing (Specification 5)
// ---------------------------------------------------------------------------
constexpr uint32_t CONTROL_PERIOD_MS = 100;  // exactly 100 ms, vTaskDelayUntil
constexpr int CONTROL_TASK_CORE = 1;
constexpr int CONTROL_TASK_PRIORITY = 20;    // highest application task
constexpr uint32_t CONTROL_TASK_STACK = 6144;
constexpr int NET_TASK_CORE = 0;
constexpr int NET_TASK_PRIORITY = 3;
constexpr uint32_t NET_TASK_STACK = 8192;
constexpr int SERIAL_TASK_CORE = 0;
constexpr int SERIAL_TASK_PRIORITY = 2;
constexpr uint32_t SERIAL_TASK_STACK = 4096;
// Task watchdog on the control task. A stall resets the board; pull-downs then
// force pump OFF / valve OPEN. IDF 4.4 takes whole seconds.
constexpr uint32_t CONTROL_WDT_TIMEOUT_S = 1;

// ---------------------------------------------------------------------------
// Sensor validity (Section 5.2.2)
// ---------------------------------------------------------------------------
constexpr float P_VALID_MIN = -2.0f;   // below this: invalid (range check)
constexpr float P_RANGE_MAX = 31.0f;   // above this is still VALID and trips HARD_LIMIT (Q4)
constexpr float P_MAX_DROP_PER_SAMPLE = 2.0f;  // broken wire reads 0 V; blow-down is <= 0.08 mbar/sample
constexpr uint32_t SENSOR_TIMEOUT_MS = 500;    // no valid sample for > 0.5 s -> SENSOR_LOSS
// New-reference rule after a drop rejection (Q5). Only allowed while TRIPPED.
constexpr int REREF_SAMPLES = 10;          // consecutive in-range samples (1 s)
constexpr float REREF_MAX_SPREAD = 0.5f;   // max - min of those samples [mbar]
// Low-pressure blind-spot plausibility check (Q5): a broken wire below 2 mbar(g)
// passes the drop check. In RUN with the pump ON the pressure must rise.
constexpr float PLAUS_MIN_PRESSURE = 1.0f;   // mbar(g)
constexpr uint32_t PLAUS_TIMEOUT_MS = 30000; // 30 s

// Light filter so P_f cannot mask a rapid rise (Section 4.3.1.2 Eq. 11, alpha >= 0.5).
constexpr float PF_ALPHA = 0.5f;

// ---------------------------------------------------------------------------
// Safety / trip (Specification 6, Integrated Spec 3, Table D)
// ---------------------------------------------------------------------------
constexpr float P_SOFT_LIMIT = 20.0f;  // software safety limit
constexpr float P_HARD_LIMIT = 29.0f;  // hard trip (Table D layer 4)
// DISCREPANCY, pending CHE confirmation:
//   Specification 6 says injection stops AND the vent opens at the 20 mbar(g) limit.
//   Table D layer 3 (20 mbar) only opens the vent; pump OFF is layer 4 (29 mbar).
// true  -> follow Spec 6: pump OFF at 20 mbar(g).
// false -> follow Table D: at 20 only the vent opens; pump stops at 29, on sensor loss,
//          or on any later escalation.
constexpr bool PUMP_OFF_AT_SOFT_LIMIT = true;
// Optional lag compensation for the ~1 s QBM2030 reaction (Section 5.2.2, I6b fallback):
// soft trip on max(P, P + LAG_COMP_S * dP/dt). Off by default.
constexpr bool LAG_COMP_ENABLED = false;
constexpr float LAG_COMP_S = 1.0f;
// Trip reset (Section 4.3.1.2 / Fig. 7 rule 7): P < 20, valid non-stale sample,
// deliberate local operator action (serial RESET). Strict "<".
constexpr float RESET_MAX_PRESSURE = 20.0f;

// ---------------------------------------------------------------------------
// PI control (Integrated Spec 2, HYSYS gains, Section 5.1)
// ---------------------------------------------------------------------------
constexpr float SETPOINT = 10.0f;  // mbar(g)
constexpr float KC = 36.0f;        // % per mbar
constexpr float TI_S = 8.0f;       // s
constexpr float TS_S = 0.1f;       // s (= CONTROL_PERIOD_MS)

// Valve PWM: time-proportioned, <= 10 Hz (Table C). 14-bit: 10-bit cannot reach
// 10 Hz from the 80 MHz APB clock (divider limit).
constexpr uint32_t VALVE_PWM_HZ = 10;
constexpr uint8_t VALVE_PWM_BITS = 14;
constexpr uint8_t VALVE_LEDC_CHANNEL = 0;

// ---------------------------------------------------------------------------
// Early warning (Integrated Spec 1, Section 4.3.1.2) - display only
// ---------------------------------------------------------------------------
constexpr float P_WARNING = 14.0f;            // fires immediately
constexpr float WARN_HORIZON_S = 12.0f;       // t_to_limit <= 12 s
constexpr float MIN_WARNING_SLOPE = 0.05f;    // mbar/s (Q2, user-approved)
constexpr int WARN_CONFIRM_SAMPLES = 3;       // consecutive samples for the slope rule
constexpr int SLOPE_WINDOW = 5;               // 5-sample linear regression

// ---------------------------------------------------------------------------
// Communication (Section 3 of the brief). Local AP only, no Internet (Constraint 4).
// ---------------------------------------------------------------------------
#define WIFI_AP_SSID "M021-RIG"
#define WIFI_AP_PASSWORD "CHANGE_ME"  // >= 8 chars (WPA2)
constexpr uint8_t WIFI_AP_CHANNEL = 6;
constexpr uint8_t WIFI_AP_MAX_CLIENTS = 2;
// AP address 192.168.4.1 / 24
constexpr uint8_t WIFI_AP_IP[4] = {192, 168, 4, 1};
constexpr uint16_t WS_PORT = 81;

// Ring buffer: >= 1200 samples (>= 120 s at 10 Hz). 1500 = 150 s.
constexpr uint32_t RING_SAMPLES = 1500;
// A sample sent more than this many samples behind the newest one is flagged replayed.
constexpr uint32_t REPLAY_LAG_SAMPLES = 3;
// Max live samples sent per network-task iteration (keeps the task responsive).
constexpr int NET_SEND_BURST = 25;
// Replay of the backlog after a (re)connect is interleaved and rate-limited, and live
// samples are always sent first, so a live sample never waits behind the backlog.
// 1500 samples drain in about 1500 / (100 - 10) = 17 s.
constexpr uint32_t REPLAY_RATE_HZ = 100;  // replayed samples per second, per client

// ---------------------------------------------------------------------------
// Serial
// ---------------------------------------------------------------------------
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint32_t TEST_PRINT_PERIOD_MS = 200;  // 5 Hz in TEST mode
constexpr int ZERO_AVERAGE_SAMPLES = 10;        // ZERO averages the last 1 s
// ZERO is refused if the measured offset is larger than this (10 % of full scale):
// the vessel is probably not vented, or the range jumper is wrong. (A13, user-approved)
// "ZERO FORCE" overrides it with a warning; zero_forced is then logged in every sample.
constexpr float ZERO_MAX_ABS_OFFSET = 3.0f;     // mbar
