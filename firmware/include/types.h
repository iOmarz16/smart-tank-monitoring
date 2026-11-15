// Shared types for the control task, network task and serial task.
#pragma once

#include <stdint.h>

enum class State : uint8_t { BOOT = 0, IDLE, RUN, TEST, TRIPPED };
enum class TripReason : uint8_t { NONE = 0, SOFT_LIMIT, SENSOR_LOSS, HARD_LIMIT };

const char *stateName(State s);
const char *tripReasonName(TripReason r);

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
  State state;
  TripReason trip_reason;
  bool valid;
  bool sensor_fault;  // invalid sample, not yet stale (< 0.5 s): PI frozen, duty held
  bool pump_on;
  bool warning;
  bool trip;
};

// Commands from the serial task to the control task. Only the control task
// changes state or outputs.
enum class CmdType : uint8_t {
  ZERO,
  START,
  STOP,
  RESET,
  STATUS,
  TEST_ENTER,
  TEST_PUMP,    // arg 0/1
  TEST_VALVE,   // arg 0 = release (open), 1 = energize (closed)
  TEST_DUTY,    // arg 0..100 = energized (closed) duty %
  TEST_EXIT,
};

struct Command {
  CmdType type;
  int arg;
};

// Reply text from the control task back to the serial task.
struct Reply {
  char text[200];
  bool zero_changed;   // serial task persists zero_offset to NVS
  float zero_offset;
};
