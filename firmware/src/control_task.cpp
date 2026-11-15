// Control task: core 1, high priority, period exactly 100 ms (vTaskDelayUntil).
// Never waits on the network or the serial port.
#include <Arduino.h>
#include <esp_task_wdt.h>
#include <esp_timer.h>
#include <string.h>

#include "config.h"
#include "outputs.h"
#include "readback.h"
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

// t3 for test I3a: the moment the outputs actually changed after a trip.
struct T3Tracker {
  bool pending = false;
  bool need_valve = false;  // valve MOSFET was ON at the decision -> wait for its OFF edge
  bool need_pump = false;   // pump MOSFET was ON and must go OFF -> wait for its OFF edge
  uint64_t decision_us = 0;
  uint64_t t3_us = 0;
  T3Source source = T3Source::NONE;

  void clear() { *this = T3Tracker(); }

  void poll(uint64_t now_us) {
    if (!pending) return;
    const uint64_t tv = readback::lastValveOffUs();
    const uint64_t tp = readback::lastPumpOffUs();
    const bool v_ok = !need_valve || tv > decision_us;
    const bool p_ok = !need_pump || tp > decision_us;
    if (v_ok && p_ok) {
      uint64_t t = decision_us;  // nothing to switch: outputs were already OFF
      if (need_valve && tv > t) t = tv;
      if (need_pump && tp > t) t = tp;
      t3_us = t;
      source = T3Source::READBACK;
      pending = false;
    } else if (now_us - decision_us > (uint64_t)READBACK_TIMEOUT_MS * 1000ULL) {
      t3_us = 0;
      source = T3Source::READBACK_TIMEOUT;
      pending = false;
    }
  }
};

void controlTask(void *) {
  static Supervisor sup(clockUs);
  static T3Tracker t3;

  // Review m6: analogReadMilliVolts() characterizes with the GLOBAL attenuation, not the
  // per-pin one, so set both to 11 dB (they must never differ).
  analogSetAttenuation(ADC_11db);
  analogSetPinAttenuation(PIN_PT101, ADC_11db);
  if (READBACK_ENABLED) readback::init();  // ISRs attached on core 1
  sup.begin(clockUs(), g_boot_zero_offset, g_boot_zero_forced, g_boot_zero_ok);

  // Task watchdog: a stall of this task resets the board (outputs fall to the
  // safe state through the pull-downs). Reconfigures the TWDT already started by the core.
  esp_task_wdt_init(CONTROL_WDT_TIMEOUT_S, true);
  esp_task_wdt_add(nullptr);

  LedMode led_mode = LED_MODE_DEFAULT;
  bool led_state = false;
  uint32_t seq = 0;
  uint32_t prev_start_us = micros();
  TickType_t last_wake = xTaskGetTickCount();
  const TickType_t period = pdMS_TO_TICKS(CONTROL_PERIOD_MS);

  for (;;) {
    vTaskDelayUntil(&last_wake, period);
    const uint32_t start_us = micros();
    const uint64_t now_us = clockUs();
    // t0 of this sample: in SAMPLE mode the LED toggles here (I1 video cross-check).
    if (led_mode == LedMode::SAMPLE) {
      led_state = !led_state;
      outputs::led(led_state);
    }
    esp_task_wdt_reset();

    bool read_ok = false;
    const float mv = readSensorMv(&read_ok);

    Command cmd;
    bool have_cmd = xQueueReceive(g_cmd_queue, &cmd, 0) == pdTRUE;
    static Reply reply;
    bool replied = false;
    if (have_cmd && cmd.type == CmdType::LED_MODE) {
      led_mode = (LedMode)cmd.arg;
      memset(&reply, 0, sizeof(reply));
      reply.id = cmd.id;
      reply.code = ReplyCode::OK_LED_MODE;
      reply.count = cmd.arg;
      have_cmd = false;
      replied = true;
    }

    // Output levels just before this step's write (which outputs were ON at a trip).
    const bool rb_valve_off_before = READBACK_ENABLED ? readback::valveOff() : true;
    const bool rb_pump_off_before = READBACK_ENABLED ? readback::pumpOff() : true;

    bool sup_replied = false;
    const Outputs o = sup.step(now_us, mv, read_ok, have_cmd ? &cmd : nullptr, &reply, &sup_replied);
    replied = replied || sup_replied;

    // Write outputs. On a trip, valveSafe() detaches LEDC and drives LOW now.
    if (o.valve_safe) outputs::valveSafe();
    else outputs::valveDuty(o.valve_duty);
    outputs::pump(o.pump_on);
    if (led_mode == LedMode::VENT) {
      led_state = o.valve_safe;  // vent commanded fully open (de-energized)
      outputs::led(led_state);
    }

    if (sup.tripDecidedThisStep()) {
      sup.setTripOutputTime(clockUs());
      const Sample &ts = sup.sample();
      t3.clear();
      t3.decision_us = ts.trip_decision_us;
      if (READBACK_ENABLED) {
        t3.pending = true;
        t3.need_valve = !rb_valve_off_before;
        t3.need_pump = !rb_pump_off_before && !o.pump_on;
      } else {
        t3.t3_us = ts.trip_output_us;
        t3.source = T3Source::SOFTWARE;
      }
    }
    if (READBACK_ENABLED) t3.poll(clockUs());

    Sample s = sup.sample();
    if (s.state != State::TRIPPED) t3.clear();
    s.seq = seq++;
    s.t_esp_ms = (uint32_t)(now_us / 1000ULL);
    s.loop_period_us = start_us - prev_start_us;
    s.t3_us = t3.t3_us;
    s.t3_source = t3.source;
    s.led_on = led_state;
    s.rb_valve_off = READBACK_ENABLED ? readback::valveOff() : false;
    s.rb_pump_off = READBACK_ENABLED ? readback::pumpOff() : false;
    s.reset_reason = g_reset_reason;
    prev_start_us = start_us;
    ring::push(s);

    if (replied) xQueueSend(g_reply_queue, &reply, 0);
  }
}
