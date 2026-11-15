// TEAM M021 - ESP32 pressure controller firmware.
// PT-101 (QBM2030-30) on GPIO34, PCV-101 normally-open vent on GPIO26 (PWM),
// P-101 pump on GPIO27. See docs/assumptions.md and include/config.h.
#include <Arduino.h>
#include <Preferences.h>
#include <esp_system.h>
#include <math.h>

#include "config.h"
#include "outputs.h"
#include "ring.h"
#include "tasks.h"

QueueHandle_t g_cmd_queue = nullptr;
QueueHandle_t g_reply_queue = nullptr;
TaskHandle_t g_control_task = nullptr;
TaskHandle_t g_net_task = nullptr;
TaskHandle_t g_serial_task = nullptr;
float g_boot_zero_offset = 0.0f;
bool g_boot_zero_forced = false;
bool g_boot_zero_ok = false;
uint8_t g_reset_reason = 0;

const char *resetReasonName(uint8_t r) {
  switch ((esp_reset_reason_t)r) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXT";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO: return "SDIO";
    default: return "UNKNOWN";
  }
}

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
  // Review m3: a watchdog/panic reset loses the RAM trip latch; make it visible.
  g_reset_reason = (uint8_t)esp_reset_reason();
  Serial.printf("reset_reason=%s%s\n", resetReasonName(g_reset_reason),
                (g_reset_reason == ESP_RST_PANIC || g_reset_reason == ESP_RST_INT_WDT ||
                 g_reset_reason == ESP_RST_TASK_WDT || g_reset_reason == ESP_RST_WDT ||
                 g_reset_reason == ESP_RST_BROWNOUT)
                    ? "  WARNING: abnormal reset, any latched trip was lost"
                    : "");

  Preferences p;
  bool has_zero = false;
  if (p.begin("m021", true)) {
    has_zero = p.isKey("zero");
    g_boot_zero_offset = p.getFloat("zero", 0.0f);
    g_boot_zero_forced = p.getBool("zforced", false);
    p.end();
  }
  if (!isfinite(g_boot_zero_offset)) {
    has_zero = false;
    g_boot_zero_offset = 0.0f;
  }
  // Review C1: START needs a valid, non-forced zero from NVS (an old value outside
  // today's +/-3 mbar limit does not count).
  g_boot_zero_ok = has_zero && !g_boot_zero_forced && fabsf(g_boot_zero_offset) <= ZERO_MAX_ABS_OFFSET;
  Serial.printf("zero_offset=%.3f mbar%s%s\n", (double)g_boot_zero_offset,
                g_boot_zero_forced ? "  WARNING: set with ZERO FORCE" : "",
                g_boot_zero_ok ? "" : "  START refused until a valid ZERO is saved");
  Serial.printf("readback=%s led_mode=%s\n", READBACK_ENABLED ? "ENABLED (GPIO32/33)" : "disabled",
                LED_MODE_DEFAULT == LedMode::VENT ? "VENT" : "SAMPLE");

  if (!outputs::initPwm()) haltSafe("LEDC setup failed");
  if (!ring::init()) haltSafe("ring buffer allocation failed");
  g_cmd_queue = xQueueCreate(4, sizeof(Command));
  g_reply_queue = xQueueCreate(2, sizeof(Reply));
  if (!g_cmd_queue || !g_reply_queue) haltSafe("queue allocation failed");

  xTaskCreatePinnedToCore(controlTask, "control", CONTROL_TASK_STACK, nullptr,
                          CONTROL_TASK_PRIORITY, &g_control_task, CONTROL_TASK_CORE);
  xTaskCreatePinnedToCore(netTask, "net", NET_TASK_STACK, nullptr, NET_TASK_PRIORITY, &g_net_task,
                          NET_TASK_CORE);
  xTaskCreatePinnedToCore(serialTask, "serial", SERIAL_TASK_STACK, nullptr, SERIAL_TASK_PRIORITY,
                          &g_serial_task, SERIAL_TASK_CORE);
}

void loop() { vTaskDelete(nullptr); }  // all work runs in the pinned tasks
