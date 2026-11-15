// TEAM M021 - ESP32 pressure controller firmware.
// PT-101 (QBM2030-30) on GPIO34, PCV-101 normally-open vent on GPIO26 (PWM),
// P-101 pump on GPIO27. See docs/assumptions.md and include/config.h.
#include <Arduino.h>
#include <Preferences.h>

#include "config.h"
#include "outputs.h"
#include "ring.h"
#include "tasks.h"

QueueHandle_t g_cmd_queue = nullptr;
QueueHandle_t g_reply_queue = nullptr;
float g_boot_zero_offset = 0.0f;
bool g_boot_zero_forced = false;

static void haltSafe(const char *why) {
  outputs::valveSafe();
  outputs::pump(false);
  for (;;) {
    Serial.printf("FATAL %s - outputs held safe\n", why);
    delay(2000);
  }
}

void setup() {
  // BOOT: outputs LOW before anything else (pump OFF, valve de-energized = OPEN,
  // GPIO2 LED LOW).
  outputs::initSafe();

  Serial.begin(SERIAL_BAUD);
  Serial.println(F("\nM021 controller boot"));

  Preferences p;
  if (p.begin("m021", true)) {
    g_boot_zero_offset = p.getFloat("zero", 0.0f);
    g_boot_zero_forced = p.getBool("zforced", false);
    p.end();
  }
  Serial.printf("zero_offset=%.3f mbar%s\n", (double)g_boot_zero_offset,
                g_boot_zero_forced ? "  WARNING: set with ZERO FORCE" : "");
  Serial.printf("readback=%s led_mode=%s\n", READBACK_ENABLED ? "ENABLED (GPIO32/33)" : "disabled",
                LED_MODE_DEFAULT == LedMode::VENT ? "VENT" : "SAMPLE");

  if (!outputs::initPwm()) haltSafe("LEDC setup failed");
  if (!ring::init()) haltSafe("ring buffer allocation failed");
  g_cmd_queue = xQueueCreate(4, sizeof(Command));
  g_reply_queue = xQueueCreate(2, sizeof(Reply));
  if (!g_cmd_queue || !g_reply_queue) haltSafe("queue allocation failed");

  xTaskCreatePinnedToCore(controlTask, "control", CONTROL_TASK_STACK, nullptr,
                          CONTROL_TASK_PRIORITY, nullptr, CONTROL_TASK_CORE);
  xTaskCreatePinnedToCore(netTask, "net", NET_TASK_STACK, nullptr, NET_TASK_PRIORITY, nullptr,
                          NET_TASK_CORE);
  xTaskCreatePinnedToCore(serialTask, "serial", SERIAL_TASK_STACK, nullptr, SERIAL_TASK_PRIORITY,
                          nullptr, SERIAL_TASK_CORE);
}

void loop() { vTaskDelete(nullptr); }  // all work runs in the pinned tasks
