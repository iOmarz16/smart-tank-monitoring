#include "outputs.h"

#include <Arduino.h>

#include "config.h"

namespace outputs {

static bool g_attached = false;
static uint32_t g_last_counts = UINT32_MAX;

void initSafe() {
  // Pull-downs already hold both lines LOW through reset; make it explicit.
  digitalWrite(PIN_PUMP, LOW);
  digitalWrite(PIN_VALVE, LOW);
  pinMode(PIN_PUMP, OUTPUT);
  pinMode(PIN_VALVE, OUTPUT);
  digitalWrite(PIN_PUMP, LOW);
  digitalWrite(PIN_VALVE, LOW);
}

bool initPwm() { return ledcSetup(VALVE_LEDC_CHANNEL, VALVE_PWM_HZ, VALVE_PWM_BITS) != 0; }

void pump(bool on) { digitalWrite(PIN_PUMP, on ? HIGH : LOW); }

void valveSafe() {
  if (g_attached) {
    ledcDetachPin(PIN_VALVE);
    g_attached = false;
    pinMode(PIN_VALVE, OUTPUT);
  }
  digitalWrite(PIN_VALVE, LOW);
  g_last_counts = UINT32_MAX;
}

void valveDuty(float duty_pct) {
  if (duty_pct <= 0.0f) {
    valveSafe();
    return;
  }
  const uint32_t full = 1UL << VALVE_PWM_BITS;  // value 2^bits = 100 % in core 2.x
  uint32_t counts = (uint32_t)(duty_pct / 100.0f * (float)full + 0.5f);
  if (counts > full) counts = full;
  if (!g_attached) {
    ledcAttachPin(PIN_VALVE, VALVE_LEDC_CHANNEL);
    g_attached = true;
    g_last_counts = UINT32_MAX;
  }
  if (counts != g_last_counts) {
    ledcWrite(VALVE_LEDC_CHANNEL, counts);
    g_last_counts = counts;
  }
}

}  // namespace outputs
