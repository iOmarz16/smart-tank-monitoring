// Shared types for the control task, network task and serial task.
#pragma once

#include <stdint.h>

// SIM (A35) is appended so the logged numeric values of the other states do not change.
enum class State : uint8_t { BOOT = 0, IDLE, RUN, TEST, TRIPPED, SIM };
enum class TripReason : uint8_t { NONE = 0, SOFT_LIMIT, SENSOR_LOSS, HARD_LIMIT };
// Where t3 (outputs actually changed) comes from, test I3a.
enum class T3Source : uint8_t { NONE = 0, SOFTWARE, READBACK, READBACK_TIMEOUT };

const char *stateName(State s);
const char *tripReasonName(TripReason r);
const char *t3SourceName(T3Source s);

// One record per 100 ms control loop. Stored in the ring buffer before it is
// forwarded, so a Wi-Fi outage delays but does not remove log data (Spec 5).
struct Sample {
  uint32_t seq;             // strictly increasing, starts at 0 after boot
  uint32_t t_esp_ms;        // ESP32 clock (esp_timer) at loop start
  uint32_t loop_period_us;  // micros() between this loop start and the previous one
  float v_gpio_mv;          // averaged ADC reading at GPIO34
  float p_raw;              // mbar(g) control pressure: the real one, or the simulated one in SIM (A35)
  float p_f;                // filtered control pressure, held while invalid
  float p_real;             // mbar(g) REAL sensor pressure, zero applied (= p_raw outside SIM; NaN if read failed)
  float p_f_real;           // internal (not in the JSON): real filtered pressure, input of the leak model
  bool simulated;           // this sample's control pressure is the SIM RAMP pressure (A35)
  uint8_t sim_rate;         // mbar/min of the SIM RAMP (10/20/30), 0 when not simulated
  float slope;              // 5-sample regression of P_f, mbar/s
  float t_to_limit;         // s, NaN when slope <= MIN_WARNING_SLOPE
  float u_open;             // vent opening %, 0..100
  float integ;              // PI integrator sum (sum of e, mbar*samples), after update
  float valve_duty;         // energized (closed) duty %, = 100 - u_open in RUN
  float zero_offset;        // mbar, active zero offset
  uint64_t trip_decision_us;  // esp_timer us when the trip was decided (0 = none)
  uint64_t trip_output_us;    // esp_timer us right after the safe outputs were written
  uint64_t t3_us;             // I3a t3: read-back edge (READBACK) or = trip_output_us (SOFTWARE)
  State state;
  TripReason trip_reason;
  T3Source t3_source;
  bool valid;
  bool sensor_fault;  // invalid sample, not yet stale (< 0.5 s): PI frozen, duty held
  bool pump_on;
  bool warning;
  bool trip;
  bool led_on;        // GPIO2 level written in this loop
  bool zero_forced;   // active zero offset was set with ZERO FORCE (outside +/-3 mbar)
  bool rb_valve_off;  // read-back: valve MOSFET OFF (only if READBACK_ENABLED)
  bool rb_pump_off;   // read-back: pump MOSFET OFF (only if READBACK_ENABLED)
  uint8_t reset_reason;    // esp_reset_reason() at boot (same value in every sample)
  bool test_pump_timeout;  // internal (not in the JSON): TEST pump switched OFF at the end of its duration
  bool trip_latched;       // tripped and only RESET can clear it (A30); false while a SOFT_LIMIT trip may auto-clear
  uint8_t auto_clears;     // SOFT_LIMIT auto-clears within the last SOFT_AUTOCLEAR_WINDOW_S
  bool auto_clear_event;   // internal (not in the JSON): a SOFT_LIMIT trip auto-cleared in this step
  // Team leak model (A34), LOG AND DISPLAY ONLY. Filled by the control task after the
  // supervisor step (src/leak_model.h); the supervisor never reads them.
  float p_corr;            // mbar(g), P_f + PUMP_ON_OFFSET while the pump ran during the read
  float p_comp_assumed;    // mbar(g), ASSUMED no-leak estimate; NOT the vessel pressure
  float leak_rate;         // mbar/s, k * sqrt(P_corr)
  bool leak_pump_on;       // internal (not in the JSON): pump state the model used (last loop's command)
};

// Commands from the serial task to the control task. Only the control task
// changes state or outputs.
enum class CmdType : uint8_t {
  ZERO,         // arg 1 = FORCE (override the +/-3 mbar limit, not the +/-5 cap)
  START,
  STOP,
  RESET,
  STATUS,
  TEST_ENTER,
  TEST_PUMP,    // arg 0 = OFF, 1..TEST_PUMP_MAX_S = ON for that many s, < 0 = invalid duration
  TEST_VALVE,   // arg 0 = release (open), 1 = energize (closed)
  TEST_DUTY,    // arg 0..100 = energized (closed) duty %
  TEST_EXIT,
  SIM_RAMP,     // arg = mbar/min (10, 20 or 30; anything else is refused)
  SIM_EXIT,
  LED_MODE,     // arg = LedMode; handled by the control task glue, not the supervisor
  ZERO_UNSAVED, // internal: the serial task could not persist the new zero to NVS
};

struct Command {
  CmdType type;
  int arg;
  uint32_t id;  // echoed in the Reply, so a late reply is never taken for the next one
};

// The control task only fills codes and numbers; the serial task (core 0) does all
// text formatting, so no float printf runs in the control task (review m9).
enum class ReplyCode : uint8_t {
  ERR_UNKNOWN,
  OK_LED_MODE,         // count = LedMode
  ERR_ZERO_STATE,      // state
  ERR_ZERO_DWELL,      // value = s in IDLE so far
  ERR_ZERO_SAMPLES,
  ERR_ZERO_SPREAD,     // value = max - min
  ERR_ZERO_CAP,        // value = offset
  ERR_ZERO_OFFSET,     // value = offset
  WARN_ZERO_FORCE,     // value = offset
  OK_ZERO,             // value = offset
  OK_ZERO_UNSAVED,
  ERR_START_STATE,     // state
  ERR_START_SENSOR,
  ERR_START_NO_ZERO,   // flag = active zero was forced
  OK_RUN,
  OK_STOP,
  ERR_STOP_TRIPPED,    // reason
  OK_ALREADY,          // state
  ERR_RESET_STATE,     // state
  ERR_RESET_SENSOR,
  ERR_RESET_PRESSURE,  // value = P_raw
  OK_RESET,
  STATUS,              // status
  ERR_TEST_STATE,      // state
  OK_TEST,
  ERR_NOT_TEST,
  OK_TEST_SET,         // flag = pump, value = duty, count = pump duration s (when ON)
  ERR_TEST_DURATION,   // TEST PUMP ON duration outside 1..TEST_PUMP_MAX_S; pump unchanged
  OK_TEST_EXIT,
  ERR_TEST_EXIT,
  ERR_SIM_STATE,       // state
  ERR_SIM_RATE,        // count = requested rate
  ERR_SIM_SENSOR,
  ERR_SIM_NO_ZERO,     // flag = active zero was forced
  OK_SIM,              // count = rate, value = start pressure
  OK_SIM_EXIT,
  ERR_SIM_EXIT,        // state
};

struct StatusSnapshot {
  State state;
  TripReason reason;
  float p_raw, p_f, p_real, v_gpio_mv, u_open, integ, duty, zero_offset;
  uint8_t sim_rate;  // 0 when not in SIM
  bool valid, pump_on, warning, zero_forced, zero_ok, trip_latched;
  uint8_t auto_clears;
  uint32_t idle_s;  // s in IDLE (0 outside IDLE)
  uint64_t trip_decision_us, trip_output_us;
};

// Reply from the control task back to the serial task.
struct Reply {
  uint32_t id;  // = Command.id
  ReplyCode code;
  State state;        // state before the command
  TripReason reason;
  float value;
  int count;
  bool flag;
  bool zero_changed;  // serial task persists zero_offset to NVS
  bool zero_forced;
  float zero_offset;
  StatusSnapshot status;  // only for ReplyCode::STATUS
};
