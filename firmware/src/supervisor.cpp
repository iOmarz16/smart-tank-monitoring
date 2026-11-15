#include "supervisor.h"

#include <math.h>
#include <string.h>

#include "config.h"

static_assert(SLOPE_WINDOW <= 8, "window arrays hold 8 samples");
static_assert(ZERO_AVERAGE_SAMPLES <= 16, "zero history holds 16 samples");
static_assert(ZERO_FORCE_MAX_ABS_OFFSET >= ZERO_MAX_ABS_OFFSET, "FORCE cap below the normal limit");

const char *stateName(State s) {
  switch (s) {
    case State::BOOT: return "BOOT";
    case State::IDLE: return "IDLE";
    case State::RUN: return "RUN";
    case State::TEST: return "TEST";
    case State::TRIPPED: return "TRIPPED";
  }
  return "?";
}

const char *tripReasonName(TripReason r) {
  switch (r) {
    case TripReason::NONE: return "NONE";
    case TripReason::SOFT_LIMIT: return "SOFT_LIMIT";
    case TripReason::SENSOR_LOSS: return "SENSOR_LOSS";
    case TripReason::HARD_LIMIT: return "HARD_LIMIT";
  }
  return "?";
}

const char *t3SourceName(T3Source s) {
  switch (s) {
    case T3Source::NONE: return "NONE";
    case T3Source::SOFTWARE: return "SOFTWARE";
    case T3Source::READBACK: return "READBACK";
    case T3Source::READBACK_TIMEOUT: return "READBACK_TIMEOUT";
  }
  return "?";
}

static float clampf(float x, float lo, float hi) { return x < lo ? lo : (x > hi ? hi : x); }

Supervisor::Supervisor(ClockUs clock) : clock_(clock) {}

void Supervisor::begin(uint64_t now_us, float zero_offset, bool zero_forced, bool zero_ok) {
  zero_offset_ = zero_offset;
  zero_forced_ = zero_forced;
  zero_ok_ = zero_ok && !zero_forced;
  t0_us_ = now_us;
  // No valid sample yet: the 0.5 s staleness timer starts at boot.
  last_valid_us_ = now_us;
  setState(State::BOOT, now_us);
}

void Supervisor::setState(State s, uint64_t now_us) {
  if (s != state_ || s == State::BOOT) {
    idle_since_us_ = now_us;
    zero_hist_n_ = 0;
    zero_hist_i_ = 0;
  }
  state_ = s;
}

bool Supervisor::stale(uint64_t now_us) const {
  return (now_us - last_valid_us_) > (uint64_t)SENSOR_TIMEOUT_MS * 1000ULL;
}

// ---------------------------------------------------------------------------
// 1. Acquisition and validity (Section 5.2.2)
// ---------------------------------------------------------------------------
void Supervisor::validate(uint64_t now_us, float v_gpio_mv, bool read_ok) {
  valid_ = false;
  read_ok_ = read_ok;
  v_gpio_mv_ = v_gpio_mv;
  // ZERO window: consecutive valid IDLE samples only. Any invalid sample restarts it
  // (reset here; refilled below only if this sample turns out valid and in IDLE).
  const int zero_n_before = zero_hist_n_;
  const int zero_i_before = zero_hist_i_;
  zero_hist_n_ = 0;
  zero_hist_i_ = 0;

  if (!read_ok) {
    p_raw_ = NAN;
    reref_count_ = 0;
    return;
  }
  const float p_uncorr = (v_gpio_mv / 1000.0f) * DIVIDER_RATIO * SENSOR_MBAR_PER_V;
  const float p = p_uncorr - zero_offset_;
  p_raw_ = p;

  if (p < P_VALID_MIN) {
    // Range check. Readings above P_RANGE_MAX are deliberately NOT rejected:
    // over-range is high pressure and must trip HARD_LIMIT (Q4).
    reref_count_ = 0;
    return;
  }

  bool ok = true;
  if (has_ref_ && (last_valid_p_ - p) > P_MAX_DROP_PER_SAMPLE) {
    // Physically impossible drop: a broken wire reads 0 V (inside the range).
    ok = false;
    // New-reference rule (Q5): REREF_SAMPLES consecutive in-range samples that
    // agree within REREF_MAX_SPREAD. Only accepted while the trip is latched.
    if (reref_count_ == 0) {
      reref_min_ = reref_max_ = p;
      reref_count_ = 1;
    } else {
      reref_min_ = fminf(reref_min_, p);
      reref_max_ = fmaxf(reref_max_, p);
      if (reref_max_ - reref_min_ > REREF_MAX_SPREAD) {
        reref_min_ = reref_max_ = p;
        reref_count_ = 1;
      } else {
        reref_count_++;
      }
    }
    if (state_ == State::TRIPPED && reref_count_ >= REREF_SAMPLES) ok = true;
  }
  if (!ok) return;

  reref_count_ = 0;
  valid_ = true;
  has_ref_ = true;
  last_valid_p_ = p;
  last_valid_us_ = now_us;

  if (state_ == State::IDLE) {
    zero_hist_n_ = zero_n_before;
    zero_hist_i_ = zero_i_before;
    zero_hist_[zero_hist_i_] = p_uncorr;
    zero_hist_i_ = (zero_hist_i_ + 1) % ZERO_AVERAGE_SAMPLES;
    if (zero_hist_n_ < ZERO_AVERAGE_SAMPLES) zero_hist_n_++;
  }

  // Light first-order filter (Eq. 11).
  if (!pf_init_) {
    p_f_ = p;
    pf_init_ = true;
  } else {
    p_f_ = PF_ALPHA * p + (1.0f - PF_ALPHA) * p_f_;
  }

  // Slope window of P_f over the last SLOPE_WINDOW valid samples.
  if (win_n_ < SLOPE_WINDOW) {
    win_t_us_[win_n_] = now_us;
    win_p_[win_n_] = p_f_;
    win_n_++;
  } else {
    for (int i = 1; i < SLOPE_WINDOW; i++) {
      win_t_us_[i - 1] = win_t_us_[i];
      win_p_[i - 1] = win_p_[i];
    }
    win_t_us_[SLOPE_WINDOW - 1] = now_us;
    win_p_[SLOPE_WINDOW - 1] = p_f_;
  }
  updateWarning();
}

// ---------------------------------------------------------------------------
// Early warning (Integrated Spec 1) - display only, never changes control.
// ---------------------------------------------------------------------------
void Supervisor::updateWarning() {
  slope_ok_ = false;
  t_to_limit_ok_ = false;
  if (win_n_ == SLOPE_WINDOW) {
    // Least-squares slope with time relative to the newest sample (keeps float precision).
    const uint64_t tn = win_t_us_[SLOPE_WINDOW - 1];
    float st = 0, sp = 0, stt = 0, stp = 0;
    for (int i = 0; i < SLOPE_WINDOW; i++) {
      const float t = -(float)(tn - win_t_us_[i]) / 1e6f;
      st += t;
      sp += win_p_[i];
      stt += t * t;
      stp += t * win_p_[i];
    }
    const float n = (float)SLOPE_WINDOW;
    const float den = n * stt - st * st;
    if (den > 1e-9f) {
      slope_ = (n * stp - st * sp) / den;
      slope_ok_ = true;
    }
  }
  bool slope_rule = false;
  if (slope_ok_ && slope_ > MIN_WARNING_SLOPE) {
    t_to_limit_ = (P_SOFT_LIMIT - p_f_) / slope_;
    t_to_limit_ok_ = true;
    slope_rule = t_to_limit_ <= WARN_HORIZON_S;
  }
  warn_confirm_ = slope_rule ? warn_confirm_ + 1 : 0;
  // The fixed 14 mbar(g) rule fires immediately; the slope rule needs confirmation.
  warning_ = (p_f_ >= P_WARNING) || (warn_confirm_ >= WARN_CONFIRM_SAMPLES);
}

// ---------------------------------------------------------------------------
// 2. Operator commands (serial only; the dashboard has no authority)
//    Only codes and numbers are filled here; the serial task formats the text.
// ---------------------------------------------------------------------------
void Supervisor::execute(const Command &cmd, uint64_t now_us, Reply *r) {
  memset(r, 0, sizeof(*r));
  r->id = cmd.id;
  r->state = state_;
  r->reason = reason_;
  const bool sensor_ok = valid_ && !stale(now_us);

  switch (cmd.type) {
    case CmdType::ZERO: {
      if (state_ != State::IDLE) {
        r->code = ReplyCode::ERR_ZERO_STATE;
        return;
      }
      // Review M3: only after ZERO_MIN_IDLE_S in IDLE (blow-down finished).
      const uint64_t idle_us = now_us - idle_since_us_;
      if (idle_us < (uint64_t)ZERO_MIN_IDLE_S * 1000000ULL) {
        r->code = ReplyCode::ERR_ZERO_DWELL;
        r->value = (float)(idle_us / 1000ULL) / 1000.0f;
        return;
      }
      if (!sensor_ok || zero_hist_n_ < ZERO_AVERAGE_SAMPLES) {
        r->code = ReplyCode::ERR_ZERO_SAMPLES;
        return;
      }
      float sum = 0, lo = zero_hist_[0], hi = zero_hist_[0];
      for (int i = 0; i < ZERO_AVERAGE_SAMPLES; i++) {
        sum += zero_hist_[i];
        lo = fminf(lo, zero_hist_[i]);
        hi = fmaxf(hi, zero_hist_[i]);
      }
      // Review M3: the window must be steady, FORCE or not.
      if (hi - lo > ZERO_MAX_SPREAD) {
        r->code = ReplyCode::ERR_ZERO_SPREAD;
        r->value = hi - lo;
        return;
      }
      const float z = sum / ZERO_AVERAGE_SAMPLES;
      r->value = z;
      // Review M3: absolute cap, ZERO FORCE cannot override it.
      if (!(fabsf(z) <= ZERO_FORCE_MAX_ABS_OFFSET)) {
        r->code = ReplyCode::ERR_ZERO_CAP;
        return;
      }
      const bool beyond = fabsf(z) > ZERO_MAX_ABS_OFFSET;
      if (beyond && cmd.arg != 1) {
        r->code = ReplyCode::ERR_ZERO_OFFSET;
        return;
      }
      zero_offset_ = z;
      zero_forced_ = beyond;
      // A forced zero never counts as valid for START (review C1).
      zero_ok_ = !beyond;
      // New zero: restart the drop reference, filter and slope window (A5).
      has_ref_ = false;
      pf_init_ = false;
      win_n_ = 0;
      warn_confirm_ = 0;
      zero_hist_n_ = 0;
      zero_hist_i_ = 0;
      r->zero_changed = true;
      r->zero_forced = beyond;
      r->zero_offset = z;
      r->code = beyond ? ReplyCode::WARN_ZERO_FORCE : ReplyCode::OK_ZERO;
      return;
    }
    case CmdType::ZERO_UNSAVED:
      // NVS write failed: the offset is active in RAM but not persisted, so START is
      // refused until a ZERO is saved (review C1, "valid zero exists in NVS").
      zero_ok_ = false;
      r->code = ReplyCode::OK_ZERO_UNSAVED;
      return;
    case CmdType::START:
      if (state_ != State::IDLE) {
        r->code = ReplyCode::ERR_START_STATE;
      } else if (!sensor_ok) {
        r->code = ReplyCode::ERR_START_SENSOR;
      } else if (!zero_ok_) {
        r->code = ReplyCode::ERR_START_NO_ZERO;
        r->flag = zero_forced_;
      } else {
        setState(State::RUN, now_us);
        integ_ = 0.0f;
        plaus_low_since_us_ = 0;
        r->code = ReplyCode::OK_RUN;
      }
      return;
    case CmdType::STOP:
      if (state_ == State::RUN || state_ == State::TEST) {
        setState(State::IDLE, now_us);
        r->code = ReplyCode::OK_STOP;
      } else if (state_ == State::TRIPPED) {
        r->code = ReplyCode::ERR_STOP_TRIPPED;
      } else {
        r->code = ReplyCode::OK_ALREADY;
      }
      return;
    case CmdType::RESET:
      // Section 4.3.1.2 / Fig. 7 rule 7: P < 20 mbar(g), valid non-stale sample,
      // deliberate local operator action. Never from the dashboard.
      if (state_ != State::TRIPPED) {
        r->code = ReplyCode::ERR_RESET_STATE;
      } else if (!sensor_ok) {
        r->code = ReplyCode::ERR_RESET_SENSOR;
      } else if (!(p_raw_ < RESET_MAX_PRESSURE)) {
        r->code = ReplyCode::ERR_RESET_PRESSURE;
        r->value = p_raw_;
      } else {
        setState(State::IDLE, now_us);
        reason_ = TripReason::NONE;
        pump_allowed_in_trip_ = false;
        trip_decision_us_ = trip_output_us_ = 0;
        r->code = ReplyCode::OK_RESET;
      }
      return;
    case CmdType::STATUS: {
      StatusSnapshot &s = r->status;
      s.state = state_;
      s.reason = reason_;
      s.p_raw = p_raw_;
      s.p_f = p_f_;
      s.v_gpio_mv = v_gpio_mv_;
      s.u_open = u_open_;
      s.integ = integ_;
      s.duty = valve_duty_;
      s.zero_offset = zero_offset_;
      s.valid = valid_;
      s.pump_on = pump_on_;
      s.warning = warning_;
      s.zero_forced = zero_forced_;
      s.zero_ok = zero_ok_;
      s.idle_s = state_ == State::IDLE ? (uint32_t)((now_us - idle_since_us_) / 1000000ULL) : 0;
      s.trip_decision_us = trip_decision_us_;
      s.trip_output_us = trip_output_us_;
      r->code = ReplyCode::STATUS;
      return;
    }
    case CmdType::TEST_ENTER:
      if (state_ != State::IDLE) {
        r->code = ReplyCode::ERR_TEST_STATE;
      } else {
        setState(State::TEST, now_us);
        test_pump_ = false;
        test_pump_timeout_ = false;
        test_duty_ = 0.0f;
        r->code = ReplyCode::OK_TEST;
      }
      return;
    case CmdType::TEST_PUMP:
    case CmdType::TEST_VALVE:
    case CmdType::TEST_DUTY:
      if (state_ != State::TEST) {
        r->code = ReplyCode::ERR_NOT_TEST;
        return;
      }
      if (cmd.type == CmdType::TEST_PUMP) {
        test_pump_ = cmd.arg != 0;
        // Every TEST PUMP ON (re)starts the TEST_PUMP_MAX_S window (review M1).
        if (test_pump_) test_pump_since_us_ = now_us;
        test_pump_timeout_ = false;
      }
      if (cmd.type == CmdType::TEST_VALVE) test_duty_ = cmd.arg ? 100.0f : 0.0f;
      if (cmd.type == CmdType::TEST_DUTY) test_duty_ = clampf((float)cmd.arg, 0.0f, 100.0f);
      r->code = ReplyCode::OK_TEST_SET;
      r->flag = test_pump_;
      r->value = test_duty_;
      return;
    case CmdType::TEST_EXIT:
      if (state_ == State::TEST) {
        setState(State::IDLE, now_us);
        r->code = ReplyCode::OK_TEST_EXIT;
      } else {
        r->code = ReplyCode::ERR_TEST_EXIT;
      }
      return;
    case CmdType::LED_MODE:
      break;  // handled by the control task before step()
  }
  r->code = ReplyCode::ERR_UNKNOWN;
}

// ---------------------------------------------------------------------------
// 3. Trip (Spec 6, IS3, Table D). Evaluated every loop, every state, BEFORE PI.
// ---------------------------------------------------------------------------
void Supervisor::enterTrip(TripReason r) {
  const bool pump_was_on = pump_on_;
  setState(State::TRIPPED, clock_());
  reason_ = r;
  trip_new_ = true;
  trip_decision_us_ = clock_();
  trip_output_us_ = 0;
  // Spec 6 vs Table D discrepancy: see PUMP_OFF_AT_SOFT_LIMIT in config.h.
  pump_allowed_in_trip_ = !PUMP_OFF_AT_SOFT_LIMIT && r == TripReason::SOFT_LIMIT && pump_was_on;
}

void Supervisor::evaluateTrip(uint64_t now_us) {
  // Low-pressure blind-spot plausibility (Q5, review C1/M1). Armed whenever the pump
  // is commanded ON (pump_on_ = the command written last loop), in ANY state, TEST
  // included. Uses the raw pin voltage, so the zero offset cannot hide an open
  // circuit. Every completed read counts, valid or not.
  plaus_fail_ = false;
  if (pump_on_) {
    if (read_ok_) {
      if (v_gpio_mv_ < OPEN_CIRCUIT_MV_THRESHOLD) {
        if (plaus_low_since_us_ == 0) plaus_low_since_us_ = now_us;
      } else {
        plaus_low_since_us_ = 0;
      }
    }
    if (plaus_low_since_us_ != 0 &&
        (now_us - plaus_low_since_us_) > (uint64_t)PLAUS_TIMEOUT_MS * 1000ULL) {
      plaus_fail_ = true;
    }
  } else {
    plaus_low_since_us_ = 0;
  }

  const bool hard = valid_ && p_raw_ >= P_HARD_LIMIT;
  float p_soft = p_raw_;
  if (LAG_COMP_ENABLED && slope_ok_) p_soft = fmaxf(p_raw_, p_raw_ + LAG_COMP_S * slope_);
  const bool soft = valid_ && p_soft >= P_SOFT_LIMIT;
  const bool loss = stale(now_us) || plaus_fail_;

  TripReason r = TripReason::NONE;
  if (hard) r = TripReason::HARD_LIMIT;
  else if (soft) r = TripReason::SOFT_LIMIT;
  else if (loss) r = TripReason::SENSOR_LOSS;

  if (state_ != State::TRIPPED) {
    if (r != TripReason::NONE) enterTrip(r);
  } else if (pump_allowed_in_trip_ && (hard || loss)) {
    // Only reachable with PUMP_OFF_AT_SOFT_LIMIT == false: escalate, pump OFF.
    pump_allowed_in_trip_ = false;
    reason_ = hard ? TripReason::HARD_LIMIT : TripReason::SENSOR_LOSS;
  }
}

// ---------------------------------------------------------------------------
// 4. Outputs per state. PI (Integrated Spec 2) only in RUN.
// ---------------------------------------------------------------------------
Outputs Supervisor::computeOutputs(uint64_t now_us) {
  Outputs o{false, true, 0.0f};
  switch (state_) {
    case State::BOOT:
    case State::IDLE:
      u_open_ = 100.0f;
      break;
    case State::TRIPPED:
      // Vent de-energized = fully OPEN. Bypasses PI.
      u_open_ = 100.0f;
      o.pump_on = pump_allowed_in_trip_;
      break;
    case State::TEST:
      // Review M1: the pump turns OFF by itself after TEST_PUMP_MAX_S.
      if (test_pump_ && (now_us - test_pump_since_us_) > (uint64_t)TEST_PUMP_MAX_S * 1000000ULL) {
        test_pump_ = false;
        test_pump_timeout_ = true;
      }
      o.pump_on = test_pump_;
      o.valve_safe = test_duty_ <= 0.0f;
      o.valve_duty = test_duty_;
      u_open_ = 100.0f - test_duty_;
      break;
    case State::RUN:
      o.pump_on = true;
      if (valid_) {
        const float e = p_f_ - SETPOINT;
        float s = integ_ + e;
        const float u_cand = KC * (e + (TS_S / TI_S) * s);
        // Conditional integration: no accumulation while saturated in the direction of e.
        if ((u_cand > 100.0f && e > 0.0f) || (u_cand < 0.0f && e < 0.0f)) s = integ_;
        integ_ = s;
        u_open_ = clampf(KC * (e + (TS_S / TI_S) * integ_), 0.0f, 100.0f);
      }
      // else: sensor fault (< 0.5 s): integrator frozen, last duty held (Fig. 7, A4).
      o.valve_duty = 100.0f - u_open_;  // normally open: energized (closed) = 100 - u_open
      o.valve_safe = o.valve_duty <= 0.0f;
      break;
  }
  pump_on_ = o.pump_on;
  valve_duty_ = o.valve_safe ? 0.0f : o.valve_duty;
  return o;
}

// ---------------------------------------------------------------------------
Outputs Supervisor::step(uint64_t now_us, float v_gpio_mv, bool read_ok, const Command *cmd,
                         Reply *reply, bool *replied) {
  trip_new_ = false;
  if (replied) *replied = false;

  validate(now_us, v_gpio_mv, read_ok);

  if (state_ == State::BOOT && valid_) setState(State::IDLE, now_us);

  if (cmd && reply) {
    execute(*cmd, now_us, reply);
    if (replied) *replied = true;
  }

  evaluateTrip(now_us);
  const Outputs o = computeOutputs(now_us);

  Sample &s = sample_;
  s.v_gpio_mv = v_gpio_mv;
  s.p_raw = p_raw_;
  s.p_f = pf_init_ ? p_f_ : NAN;
  s.slope = slope_ok_ ? slope_ : NAN;
  s.t_to_limit = t_to_limit_ok_ ? t_to_limit_ : NAN;
  s.u_open = u_open_;
  s.integ = integ_;
  s.valve_duty = valve_duty_;
  s.zero_offset = zero_offset_;
  s.zero_forced = zero_forced_;
  s.trip_decision_us = trip_decision_us_;
  s.trip_output_us = trip_output_us_;
  s.state = state_;
  s.trip_reason = reason_;
  s.valid = valid_;
  s.sensor_fault = !valid_ && !stale(now_us);
  s.pump_on = pump_on_;
  s.warning = warning_;
  s.trip = state_ == State::TRIPPED;
  s.test_pump_timeout = test_pump_timeout_;
  return o;
}
