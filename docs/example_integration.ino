// ============================================================================
//  example_integration.ino — how to wire rig_corrections.h into the existing
//  ESP32 sketch. Copy only the marked parts into your code; rename the
//  variables marked  <-- YOUR NAME  to the ones your sketch already uses.
// ============================================================================
#include "rig_corrections.h"   // put the .h file in the same folder as the .ino

// ---- add with your other globals --------------------------------------------
rig::LeakEstimator leakEst;
rig::LeakCompensator leakComp;   // ASSUMPTION: no-leak pressure estimate
bool  prevPumpOn   = false;
bool  leakTestDone = false;

// ---- inside loop(), AFTER P_f is computed and pumpOn is known ----------------
void afterPressureRead(float P_raw, float P_f, float V_gpio_mV,
                       bool pumpOn      /* <-- YOUR NAME */,
                       bool valveClosed /* <-- YOUR NAME: true = ENERGIZED(closed) */) {

  // 1) corrected pressure (+0.19 mbar while the pump runs)
  float P_corr = rig::correctedPressure(P_f, pumpOn);

  // 1b) ASSUMPTION: pressure if there were no leak (report next to P_corr)
  float P_comp = leakComp.update(P_corr, pumpOn, valveClosed);

  // 2) leak at the current pressure
  float leak = rig::leakRate(P_corr, pumpOn);          // mbar/s

  // 3) safety (SPS6): ALWAYS on the real pressure P_corr, never on P_comp
  //    if (P_corr >= P_TRIP) { pumpOff(); ... }        // <-- YOUR CODE

  // 4) leak estimator: restarts every time the pump switches OFF
  if (prevPumpOn && !pumpOn) { leakEst.begin(); leakTestDone = false; }
  if (!pumpOn) {
    leakEst.add(P_corr);
    if (!leakTestDone && leakEst.ready() && P_corr < 0.25f) {
      Serial.printf("EVENT LEAK k=%.4f mbar^0.5/s R2=%.3f n=%d\n",
                    leakEst.k(), leakEst.r2(), leakEst.points());
      leakTestDone = true;
    }
  }
  prevPumpOn = pumpOn;

  // 5) log line: original columns kept, new columns added at the end
  Serial.printf("TEST P_raw=%.3f P_f=%.3f P_corr=%.3f P_comp_assumed=%.3f leak=%.3f V_gpio=%.1fmV pump=%s\n",
                P_raw, P_f, P_corr, P_comp, leak, V_gpio_mV, pumpOn ? "ON" : "OFF");
}

// ---- optional: for the controller / ML --------------------------------------
// seconds until pressure drops from P_corr to the lower limit with pump OFF:
//   float tLeft = rig::timeToDropOff(P_corr, P_LOW);   // <-- YOUR LIMIT
// predicted pressure 2 s from now with pump OFF:
//   float P_2s  = rig::predictPressureOff(P_corr, 2.0f);
