#include "readback.h"

#include <Arduino.h>
#include <esp_timer.h>

#include "config.h"

namespace readback {

static portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile uint64_t g_valve_off_us = 0;
static volatile uint64_t g_pump_off_us = 0;

static void IRAM_ATTR onValveRise() {
  const uint64_t t = (uint64_t)esp_timer_get_time();
  portENTER_CRITICAL_ISR(&g_mux);
  g_valve_off_us = t;
  portEXIT_CRITICAL_ISR(&g_mux);
}

static void IRAM_ATTR onPumpRise() {
  const uint64_t t = (uint64_t)esp_timer_get_time();
  portENTER_CRITICAL_ISR(&g_mux);
  g_pump_off_us = t;
  portEXIT_CRITICAL_ISR(&g_mux);
}

void init() {
  // GPIO32/33: plain inputs, no internal pulls (the external divider sets the level).
  pinMode(PIN_RB_VALVE, INPUT);
  pinMode(PIN_RB_PUMP, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_RB_VALVE), onValveRise, RISING);
  attachInterrupt(digitalPinToInterrupt(PIN_RB_PUMP), onPumpRise, RISING);
}

bool valveOff() { return digitalRead(PIN_RB_VALVE) == HIGH; }
bool pumpOff() { return digitalRead(PIN_RB_PUMP) == HIGH; }

uint64_t lastValveOffUs() {
  portENTER_CRITICAL(&g_mux);
  const uint64_t t = g_valve_off_us;
  portEXIT_CRITICAL(&g_mux);
  return t;
}

uint64_t lastPumpOffUs() {
  portENTER_CRITICAL(&g_mux);
  const uint64_t t = g_pump_off_us;
  portEXIT_CRITICAL(&g_mux);
  return t;
}

}  // namespace readback
