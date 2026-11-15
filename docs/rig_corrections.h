// ============================================================================
//  rig_corrections.h  —  Team M021 pressure-pot rig (ESP32)
//  Measured corrections and leak model from the rig tests of 15-Nov-2026.
//
//  Source data (all at Ts = 0.2 s per log line):
//   * Test A: pump ON 300 s -> OFF, valve closed, needle valve OPEN
//       decay 3.37 -> 0 mbar in ~11.6 s, sqrt(P) linear in time (R2 = 0.999)
//       -> k = 0.28 mbar^0.5/s  (pot leak + backflow through pump line)
//   * Test B: pump ON 60 s, needle valve CLOSED, then pump OFF
//       decay 1.94 -> 0 mbar in ~48 s, sqrt(P) linear (R2 = 0.993)
//       -> k = 0.045 mbar^0.5/s (real pot leak only)
//   * Both tests: reading jumps +0.19 mbar within one sample when the pump
//       switches OFF -> reading is 0.19 mbar low while the pump runs.
//
//  Leak law (orifice type):   dP/dt = -k * sqrt(P)     [P in mbar(g)]
//  Closed-form solution:      sqrt(P(t)) = sqrt(P0) - (k/2) * t
// ============================================================================
#pragma once
#include <math.h>

namespace rig {

// ---- Measured constants ----------------------------------------------------
constexpr float TS_S            = 0.2f;   // log/loop period (s)
constexpr float PUMP_ON_OFFSET  = 0.19f;  // mbar, added while pump is ON
constexpr float K_LEAK_PUMP_OFF = 0.28f;  // mbar^0.5/s, pump OFF, no check valve
constexpr float K_LEAK_PUMP_ON  = 0.045f; // mbar^0.5/s, pot leak only
// After a check valve / inlet solenoid is installed, set
// K_LEAK_PUMP_OFF = K_LEAK_PUMP_ON (backflow path removed).

// ---- 1) Reading correction -------------------------------------------------
// P_f    : filtered pressure from the sensor (mbar)
// pumpOn : actual pump state in this loop iteration
inline float correctedPressure(float P_f, bool pumpOn) {
  return P_f + (pumpOn ? PUMP_ON_OFFSET : 0.0f);
}

// ---- 2) Leak model -----------------------------------------------------------
inline float leakK(bool pumpOn) {
  return pumpOn ? K_LEAK_PUMP_ON : K_LEAK_PUMP_OFF;
}

// Leak rate at pressure P (mbar/s), positive number = pressure loss per second
inline float leakRate(float P, bool pumpOn) {
  return (P > 0.0f) ? leakK(pumpOn) * sqrtf(P) : 0.0f;
}

// Leak over one loop step (mbar per Ts)
inline float leakPerStep(float P, bool pumpOn) {
  return leakRate(P, pumpOn) * TS_S;
}

// Predicted pressure after dt seconds with the pump OFF (leak only)
inline float predictPressureOff(float P0, float dt) {
  if (P0 <= 0.0f) return 0.0f;
  float s = sqrtf(P0) - 0.5f * K_LEAK_PUMP_OFF * dt;
  return (s > 0.0f) ? s * s : 0.0f;
}

// Seconds until the pressure falls from P0 to Pth with the pump OFF
// (useful for deciding when to switch the pump back on)
inline float timeToDropOff(float P0, float Pth) {
  if (P0 <= Pth) return 0.0f;
  if (Pth < 0.0f) Pth = 0.0f;
  return 2.0f * (sqrtf(P0) - sqrtf(Pth)) / K_LEAK_PUMP_OFF;
}

// ---- 3) On-board leak test: re-measure k from any pump-OFF decay ------------
// Usage: call begin() when the pump switches OFF, add(P) every loop while the
// pump stays OFF, then read k() / r2(). Only points with 0.2 < P < 3.5 mbar are
// used (avoids the ADC floor near zero and the pump-off step at the start).
class LeakEstimator {
 public:
  void begin() { n_ = 0; t_ = 0; sx_ = sy_ = sxx_ = sxy_ = syy_ = 0; skip_ = 3; }

  void add(float P) {
    float t = t_ * TS_S;
    t_++;
    if (skip_ > 0) { skip_--; return; }          // skip pump-off transient
    if (P <= 0.2f || P >= 3.5f) return;
    float y = sqrtf(P);
    n_++; sx_ += t; sy_ += y; sxx_ += t * t; sxy_ += t * y; syy_ += y * y;
  }

  bool ready() const { return n_ >= 20; }

  // k = -2 * slope of sqrt(P) vs t
  float k() const {
    float d = n_ * sxx_ - sx_ * sx_;
    if (n_ < 2 || d == 0) return NAN;
    float slope = (n_ * sxy_ - sx_ * sy_) / d;
    return -2.0f * slope;
  }

  float r2() const {
    float dx = n_ * sxx_ - sx_ * sx_;
    float dy = n_ * syy_ - sy_ * sy_;
    if (n_ < 2 || dx <= 0 || dy <= 0) return NAN;
    float c = n_ * sxy_ - sx_ * sy_;
    return (c * c) / (dx * dy);
  }

  int points() const { return n_; }

 private:
  int n_ = 0, t_ = 0, skip_ = 3;
  double sx_ = 0, sy_ = 0, sxx_ = 0, sxy_ = 0, syy_ = 0;
};

// ---- 4) ASSUMPTION: leak-compensated pressure ("no-leak" estimate) ---------
// P_comp = real corrected pressure + all pressure lost to the measured leak
// since the pot was last empty.
//
//  !! This is an ASSUMPTION, not a measurement. It is NOT the pot pressure.
//  !! It keeps growing while the pump runs at its plateau (~3.4 mbar), because
//  !! it assumes the pump could keep supplying the leaked air. Report it with
//  !! the real pressure next to it. Never use it for the safety trip.
//
// Rules:
//  * accumulates only while the vent valve is CLOSED (venting is intentional,
//    not a leak)
//  * resets when the pump is OFF and the real pressure is back near zero
class LeakCompensator {
 public:
  void reset() { lost_ = 0.0f; }

  // P_corr     : corrected real pressure (correctedPressure())
  // pumpOn     : pump state
  // valveClosed: true when the vent valve is ENERGIZED(closed)
  // returns P_comp (mbar)
  float update(float P_corr, bool pumpOn, bool valveClosed) {
    if (!pumpOn && P_corr < 0.05f) lost_ = 0.0f;          // pot empty -> reset
    if (valveClosed) lost_ += leakPerStep(P_corr, pumpOn);
    return P_corr + lost_;
  }

  float lost() const { return lost_; }  // mbar added so far

 private:
  float lost_ = 0.0f;
};

}  // namespace rig
