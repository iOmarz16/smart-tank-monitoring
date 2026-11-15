// Shared types for the control task, network task and serial task.
#pragma once

#include <stdint.h>

enum class State : uint8_t { BOOT = 0, IDLE, RUN, TEST, TRIPPED };
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
  float p_raw;              // mbar(g), zero offset applied (NaN if read failed)
  float p_f;                // filtered pressure, held while invalid
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
};

// Commands from the serial task to the control task. Only the control task
// changes state or outputs.
enum class CmdType : uint8_t {
  ZERO,         // arg 1 = FORCE (override the +/-3 mbar limit)
  START,
  STOP,
  RESET,
  STATUS,
  TEST_ENTER,
  TEST_PUMP,    // arg 0/1
  TEST_VALVE,   // arg 0 = release (open), 1 = energize (closed)
  TEST_DUTY,    // arg 0..100 = energized (closed) duty %
  TEST_EXIT,
  LED_MODE,     // arg = LedMode; handled by the control task glue, not the supervisor
};

struct Command {
  CmdType type;
  int arg;
};

// Reply text from the control task back to the serial task.
struct Reply {
  char text[200];
  bool zero_changed;   // serial task persists zero_offset to NVS
  bool zero_forced;
  float zero_offset;
};
