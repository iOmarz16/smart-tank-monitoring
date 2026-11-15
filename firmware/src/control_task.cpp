// Control task: core 1, high priority, period exactly 100 ms (vTaskDelayUntil).
// Never waits on the network or the serial port.
#include <Arduino.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>

#include "config.h"
#include "outputs.h"
#include "ring.h"
#include "supervisor.h"
#include "tasks.h"

static uint64_t clockUs() { return (uint64_t)esp_timer_get_time(); }

static float readSensorMv(bool *ok) {
  uint32_t sum = 0;
  for (int i = 0; i < ADC_AVERAGE_READS; i++) sum += analogReadMilliVolts(PIN_PT101);
  *ok = true;  // analogReadMilliVolts has no failure return on ESP32
  return (float)sum / (float)ADC_AVERAGE_READS;
}

void controlTask(void *) {
  static Supervisor sup(clockUs);

  analogSetPinAttenuation(PIN_PT101, ADC_11db);
  sup.begin(clockUs(), g_boot_zero_offset);

  // Task watchdog: a stall of this task resets the board (outputs fall to the
  // safe state through the pull-downs). Reconfigures the TWDT already started by the core.
  esp_task_wdt_init(CONTROL_WDT_TIMEOUT_S, true);
  esp_task_wdt_add(nullptr);

  uint32_t seq = 0;
  uint32_t prev_start_us = micros();
  TickType_t last_wake = xTaskGetTickCount();
  const TickType_t period = pdMS_TO_TICKS(CONTROL_PERIOD_MS);

  for (;;) {
    vTaskDelayUntil(&last_wake, period);
    const uint32_t start_us = micros();
    const uint64_t now_us = clockUs();
    esp_task_wdt_reset();

    bool read_ok = false;
    const float mv = readSensorMv(&read_ok);

    Command cmd;
    const bool have_cmd = xQueueReceive(g_cmd_queue, &cmd, 0) == pdTRUE;
    static Reply reply;
    bool replied = false;

    const Outputs o = sup.step(now_us, mv, read_ok, have_cmd ? &cmd : nullptr, &reply, &replied);

    // Write outputs. On a trip, valveSafe() detaches LEDC and drives LOW now.
    if (o.valve_safe) outputs::valveSafe();
    else outputs::valveDuty(o.valve_duty);
    outputs::pump(o.pump_on);
    if (sup.tripDecidedThisStep()) sup.setTripOutputTime(clockUs());

    Sample s = sup.sample();
    s.seq = seq++;
    s.t_esp_ms = (uint32_t)(now_us / 1000ULL);
    s.loop_period_us = start_us - prev_start_us;
    prev_start_us = start_us;
    ring::push(s);

    if (replied) xQueueSend(g_reply_queue, &reply, 0);
  }
}
