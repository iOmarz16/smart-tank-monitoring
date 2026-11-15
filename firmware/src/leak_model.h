// Team leak model (docs/rig_corrections.h, A34): LOG AND DISPLAY ONLY.
//
// !! P_comp is an ASSUMPTION, not a measurement. It is NOT the vessel pressure.
// !! Nothing computed here may reach the supervisor: trips, warning, PI and the
// !! plausibility checks run on the measured P_raw / P_f only. Only the control task
// !! includes this file, AFTER Supervisor::step(), and only writes the results into
// !! the logged Sample (tools/check_rules.py checks this).
//
// Pure logic, no Arduino calls; tools/fake_rig.py has a line-by-line Python port.
#pragma once

#include <math.h>

#include "config.h"

namespace leak {

struct Result {
  float p_corr;     // mbar(g): P_f + PUMP_ON_OFFSET while the pump ran during the read
  float p_comp;     // mbar(g), ASSUMED: P_corr + all pressure lost to the leak since "empty"
  float leak_rate;  // mbar/s: k * sqrt(P_corr), 0 at P_corr <= 0
};

// leak_rate (mbar/s) at P_corr; also used by the ring to rebuild the logged value.
inline float rate(float p_corr, bool pump_on) {
  if (isnan(p_corr)) return NAN;
  const float k = pump_on ? K_LEAK_PUMP_ON : K_LEAK_PUMP_OFF;
  return p_corr > 0.0f ? k * sqrtf(p_corr) : 0.0f;
}

class Model {
 public:
  // Call once per control loop, after Supervisor::step().
  //   p_f        : filtered pressure of this sample (NaN before the first valid sample)
  //   pump_on    : pump command that was running while this reading was taken, i.e. the
  //                one written in the PREVIOUS loop (user decision, same rule as A19)
  //   closed_duty: energized (closed) valve duty % applied during the step that ends at
  //                this reading (previous loop's command; 0 when the valve was safe/open)
  // The loss of a step is weighted by closed_duty / 100 (user decision): venting through
  // the open valve is intentional and is not counted as leak.
  Result update(float p_f, bool pump_on, float closed_duty) {
    Result r;
    if (isnan(p_f)) {  // no pressure yet: report nothing, keep the accumulated loss
      r.p_corr = r.p_comp = r.leak_rate = NAN;
      return r;
    }
    r.p_corr = p_f + (pump_on ? PUMP_ON_OFFSET : 0.0f);
    r.leak_rate = rate(r.p_corr, pump_on);
    if (!pump_on && r.p_corr < P_COMP_RESET_P) lost_ = 0.0f;  // vessel empty -> reset
    float frac = closed_duty / 100.0f;
    if (!(frac > 0.0f)) frac = 0.0f;  // also catches NaN
    if (frac > 1.0f) frac = 1.0f;
    lost_ += r.leak_rate * TS_S * frac;
    r.p_comp = r.p_corr + lost_;
    return r;
  }

 private:
  float lost_ = 0.0f;  // mbar added so far
};

static_assert(TS_S * 1000.0f == (float)CONTROL_PERIOD_MS, "leak model step must equal the loop period");

}  // namespace leak
