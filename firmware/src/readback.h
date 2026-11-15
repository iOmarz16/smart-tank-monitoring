// Optional MOSFET output read-back (test I3a, t3). Only used if READBACK_ENABLED.
// Inputs read the MOSFET OUT- terminals through dividers (README 3.1).
// Low-side switching: input HIGH = output OFF. A RISING edge = the output turned OFF.
#pragma once

#include <stdint.h>

namespace readback {
void init();                 // configures GPIO32/33 and their edge interrupts
bool valveOff();             // current level: true = valve MOSFET OFF (de-energized, vent open)
bool pumpOff();              // current level: true = pump MOSFET OFF
uint64_t lastValveOffUs();   // esp_timer us of the last rising edge (0 = none yet)
uint64_t lastPumpOffUs();
}  // namespace readback
