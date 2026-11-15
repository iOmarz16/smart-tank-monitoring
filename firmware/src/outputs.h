// MOSFET outputs: PCV-101 vent valve (GPIO26, LEDC PWM) and P-101 pump (GPIO27).
#pragma once

namespace outputs {
// Must be the first call in setup(): drives both TRIG lines LOW (pump OFF, valve OPEN).
void initSafe();
// Configure the LEDC timer for the valve (does not attach the pin).
bool initPwm();
void pump(bool on);
// Immediate de-energize: detach LEDC and drive GPIO26 LOW in this call.
// (An LEDC duty change would only take effect at the next 100 ms PWM period.)
void valveSafe();
// Energized (closed) duty in %, time-proportioned at VALVE_PWM_HZ.
void valveDuty(float duty_pct);
}  // namespace outputs
