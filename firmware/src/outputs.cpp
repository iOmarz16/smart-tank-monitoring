#include "outputs.h"

#include <Arduino.h>
#include <esp_rom_gpio.h>
#include <soc/gpio_sig_map.h>
#include <soc/gpio_struct.h>

#include "config.h"

static_assert(PIN_VALVE < 32 && PIN_PUMP < 32, "watchdog hook uses GPIO.out_w1tc (GPIO0-31)");

// Review M2. Called by the IDF 4.4 task-watchdog ISR (weak symbol in libesp_system)
// after it prints the late task names and BEFORE abort(), i.e. before the panic
// backtrace and reboot. Drives the safe state at once instead of leaving the pump ON /
// valve closed until the chip resets. ROM function + direct register writes only:
// it must work from an ISR, with interrupts off and possibly the flash cache disabled.
// It fires for any watched task (control on core 1, idle task on core 0).
extern "C" void IRAM_ATTR esp_task_wdt_isr_user_handler(void) {
  // Route GPIO26 back from LEDC to the plain GPIO output register (= ledcDetachPin).
  esp_rom_gpio_connect_out_signal(PIN_VALVE, SIG_GPIO_OUT_IDX, false, false);
  GPIO.out_w1tc = (1UL << PIN_VALVE) | (1UL << PIN_PUMP);  // valve OPEN, pump OFF
}

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
  digitalWrite(PIN_LED, LOW);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, LOW);
}

bool initPwm() { return ledcSetup(VALVE_LEDC_CHANNEL, VALVE_PWM_HZ, VALVE_PWM_BITS) != 0; }

void pump(bool on) { digitalWrite(PIN_PUMP, on ? HIGH : LOW); }

void led(bool on) { digitalWrite(PIN_LED, on ? HIGH : LOW); }

void valveSafe() {
  if (g_attached) {
    ledcDetachPin(PIN_VALVE);
    g_attached = false;
    pinMode(PIN_VALVE, OUTPUT);
    digitalWrite(PIN_VALVE, LOW);
    // Review m2: ledcAttachPin() re-applies the channel's current duty, and a new duty
    // only latches at the next PWM period. Park the detached channel at 0 % (= OPEN),
    // so a later re-attach starts open, never at an old closed duty.
    // (Never attached yet: ledcSetup() left the channel at 0 already.)
    ledcWrite(VALVE_LEDC_CHANNEL, 0);
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
