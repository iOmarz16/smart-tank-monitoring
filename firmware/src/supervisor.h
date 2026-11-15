// Pure control and safety logic (no Arduino calls). The control task feeds it one
// ADC reading per 100 ms loop; it returns the output commands and fills a Sample.
//
// Order inside step() (Section 4.3.1.2, Algorithm 1):
//   1. convert + validate the reading, update P_f, slope and warning (display only)
//   2. execute at most one operator command (serial only)
//   3. evaluate the trip (every state, BEFORE PI); a trip latches
//   4. compute outputs for the current state (PI only in RUN)
#pragma once

#include <stdint.h>

#include "types.h"

struct Outputs {
  bool pump_on;
  bool valve_safe;   // true: de-energize immediately (LEDC detached, GPIO26 LOW = OPEN)
  float valve_duty;  // energized (closed) duty %, used when valve_safe == false
};

class Supervisor {
 public:
  typedef uint64_t (*ClockUs)();

  explicit Supervisor(ClockUs clock);
  // zero_ok: a valid, non-forced zero offset exists in NVS (START is refused otherwise).
  void begin(uint64_t now_us, float zero_offset, bool zero_forced, bool zero_ok);

  // read_ok = false if the ADC read failed. cmd may be null. reply may be null;
  // if a command was executed, reply->text is filled and *replied set true.
  Outputs step(uint64_t now_us, float v_gpio_mv, bool read_ok, const Command *cmd,
               Reply *reply, bool *replied);

  // Called by the control task right after the safe outputs were written for a
  // trip decided in the same step (t3 software timestamp for test I3).
  bool tripDecidedThisStep() const { return trip_new_; }
  void setTripOutputTime(uint64_t us) { sample_.trip_output_us = us; trip_output_us_ = us; }

  const Sample &sample() const { return sample_; }

 private:
  void validate(uint64_t now_us, float v_gpio_mv, bool read_ok);
  void updateWarning();
  void execute(const Command &cmd, uint64_t now_us, Reply *reply);
  void evaluateTrip(uint64_t now_us);
  Outputs computeOutputs(uint64_t now_us);
  void enterTrip(TripReason r);
  bool stale(uint64_t now_us) const;
  // Every state change goes through here: it restarts the IDLE dwell timer and
  // clears the ZERO window, so ZERO only ever averages samples taken in IDLE.
  void setState(State s, uint64_t now_us);

  ClockUs clock_;
  State state_ = State::BOOT;
  uint64_t idle_since_us_ = 0;
  TripReason reason_ = TripReason::NONE;
  bool pump_allowed_in_trip_ = false;  // only when PUMP_OFF_AT_SOFT_LIMIT == false
  bool trip_new_ = false;
  uint64_t trip_decision_us_ = 0;
  uint64_t trip_output_us_ = 0;

  float zero_offset_ = 0.0f;
  bool zero_forced_ = false;
  bool zero_ok_ = false;  // valid non-forced zero, persisted in NVS

  // validity
  bool read_ok_ = false;
  float v_gpio_mv_ = 0.0f;
  bool valid_ = false;
  bool has_ref_ = false;
  float last_valid_p_ = 0.0f;
  uint64_t last_valid_us_ = 0;
  float p_raw_ = 0.0f;
  int reref_count_ = 0;
  float reref_min_ = 0.0f, reref_max_ = 0.0f;
  uint64_t plaus_low_since_us_ = 0;
  bool plaus_fail_ = false;

  // ZERO averaging: last uncorrected pressures from valid IDLE samples
  float zero_hist_[16] = {0};
  int zero_hist_n_ = 0;
  int zero_hist_i_ = 0;

  // filter / slope / warning
  bool pf_init_ = false;
  float p_f_ = 0.0f;
  uint64_t win_t_us_[8] = {0};
  float win_p_[8] = {0};
  int win_n_ = 0;
  float slope_ = 0.0f;
  bool slope_ok_ = false;  // slope computed (window full)
  float t_to_limit_ = 0.0f;
  bool t_to_limit_ok_ = false;
  int warn_confirm_ = 0;
  bool warning_ = false;

  // PI
  float integ_ = 0.0f;
  float u_open_ = 100.0f;
  float valve_duty_ = 0.0f;
  bool pump_on_ = false;

  // TEST mode
  bool test_pump_ = false;
  uint64_t test_pump_since_us_ = 0;  // last TEST PUMP ON
  bool test_pump_timeout_ = false;   // pump switched OFF by TEST_PUMP_MAX_S
  float test_duty_ = 0.0f;

  uint64_t t0_us_ = 0;
  Sample sample_{};
};
