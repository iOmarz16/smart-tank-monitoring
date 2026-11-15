// Serial task: core 0, low priority. Operator-only commands over USB (115200).
//   ZERO [FORCE] | START | STOP | RESET | STATUS | LED MODE SAMPLE|VENT
//   TEST | TEST PUMP ON [<s>]|OFF | TEST VALVE ENERGIZE|RELEASE | TEST DUTY <0-100> | TEST EXIT
//   SIM RAMP <10|20|30> | SIM EXIT   (A35: simulated pressure ramp, real protection stays on)
// Commands are queued to the control task, which owns all state and outputs.
// All reply text is formatted here (core 0), never in the control task (review m9).
#include <Arduino.h>
#include <Preferences.h>
#include <math.h>

#include "config.h"
#include "ring.h"
#include "tasks.h"

static uint32_t g_next_id = 1;

static void toUpper(char *s) {
  for (; *s; s++) *s = (char)toupper((unsigned char)*s);
}

static bool parse(char *line, Command *cmd) {
  toUpper(line);
  char *t1 = strtok(line, " \t");
  char *t2 = strtok(nullptr, " \t");
  char *t3 = strtok(nullptr, " \t");
  char *t4 = strtok(nullptr, " \t");
  if (!t1) return false;
  cmd->arg = 0;
  if (!strcmp(t1, "ZERO")) {
    cmd->type = CmdType::ZERO;
    if (!t2) return true;
    if (strcmp(t2, "FORCE")) return false;
    cmd->arg = 1;
    return true;
  }
  if (!strcmp(t1, "LED")) {
    if (!t2 || !t3 || strcmp(t2, "MODE")) return false;
    cmd->type = CmdType::LED_MODE;
    if (!strcmp(t3, "SAMPLE")) cmd->arg = (int)LedMode::SAMPLE;
    else if (!strcmp(t3, "VENT")) cmd->arg = (int)LedMode::VENT;
    else return false;
    return true;
  }
  if (!strcmp(t1, "START")) { cmd->type = CmdType::START; return true; }
  if (!strcmp(t1, "STOP")) { cmd->type = CmdType::STOP; return true; }
  if (!strcmp(t1, "RESET")) { cmd->type = CmdType::RESET; return true; }
  if (!strcmp(t1, "STATUS")) { cmd->type = CmdType::STATUS; return true; }
  if (!strcmp(t1, "SIM")) {
    // A35: SIM RAMP <10|20|30> | SIM EXIT. Other rates go to the supervisor (refused there).
    if (!t2 || t4) return false;
    if (!strcmp(t2, "EXIT") && !t3) { cmd->type = CmdType::SIM_EXIT; return true; }
    if (strcmp(t2, "RAMP") || !t3) return false;
    char *end = nullptr;
    const long v = strtol(t3, &end, 10);
    if (!end || *end) return false;
    cmd->type = CmdType::SIM_RAMP;
    cmd->arg = (v >= 0 && v <= 1000) ? (int)v : -1;
    return true;
  }
  if (strcmp(t1, "TEST")) return false;
  if (!t2) { cmd->type = CmdType::TEST_ENTER; return true; }
  if (!strcmp(t2, "EXIT")) { cmd->type = CmdType::TEST_EXIT; return true; }
  if (!t3) return false;
  if (!strcmp(t2, "PUMP")) {
    cmd->type = CmdType::TEST_PUMP;
    if (!strcmp(t3, "OFF")) {
      if (t4) return false;
      cmd->arg = 0;
      return true;
    }
    if (strcmp(t3, "ON")) return false;
    if (!t4) {
      cmd->arg = (int)TEST_PUMP_DEFAULT_S;
      return true;
    }
    char *end = nullptr;
    const long v = strtol(t4, &end, 10);
    if (!end || *end) return false;
    // Out-of-range durations go to the supervisor as -1: it refuses them with a reply.
    cmd->arg = (v >= 1 && v <= (long)TEST_PUMP_MAX_S) ? (int)v : -1;
    return true;
  }
  if (!strcmp(t2, "VALVE")) {
    cmd->type = CmdType::TEST_VALVE;
    if (!strcmp(t3, "ENERGIZE")) cmd->arg = 1;
    else if (!strcmp(t3, "RELEASE")) cmd->arg = 0;
    else return false;
    return true;
  }
  if (!strcmp(t2, "DUTY")) {
    char *end = nullptr;
    const long v = strtol(t3, &end, 10);
    if (!end || *end || v < 0 || v > 100) return false;
    cmd->type = CmdType::TEST_DUTY;
    cmd->arg = (int)v;
    return true;
  }
  return false;
}

static void printHelp() {
  Serial.printf("Commands: ZERO [FORCE], START, STOP, RESET, STATUS, LED MODE SAMPLE|VENT | "
                "TEST, TEST PUMP ON [<1-%lu s, default %lu>]|OFF, TEST VALVE ENERGIZE|RELEASE, "
                "TEST DUTY <0-100>, TEST EXIT | SIM RAMP <10|20|30>, SIM EXIT\n",
                (unsigned long)TEST_PUMP_MAX_S, (unsigned long)TEST_PUMP_DEFAULT_S);
}

static void formatReply(const Reply &r, char *out, size_t n) {
  const char *st = stateName(r.state);
  switch (r.code) {
    case ReplyCode::OK_LED_MODE:
      snprintf(out, n, "OK LED MODE %s",
               r.count == (int)LedMode::SAMPLE ? "SAMPLE (toggle at t0 of every sample)"
                                               : "VENT (ON while the vent is commanded fully open)");
      return;
    case ReplyCode::ERR_ZERO_STATE:
      snprintf(out, n, "ERR ZERO only in IDLE (pump OFF, valve OPEN), state=%s", st);
      return;
    case ReplyCode::ERR_ZERO_DWELL:
      snprintf(out, n, "ERR ZERO needs %lu s in IDLE (vessel vented, blow-down finished); %.0f s so far",
               (unsigned long)ZERO_MIN_IDLE_S, (double)r.value);
      return;
    case ReplyCode::ERR_ZERO_SAMPLES:
      snprintf(out, n, "ERR ZERO needs %d consecutive valid IDLE samples", ZERO_AVERAGE_SAMPLES);
      return;
    case ReplyCode::ERR_ZERO_SPREAD:
      snprintf(out, n, "ERR ZERO window not steady: max-min %.3f > %.1f mbar (still venting?)",
               (double)r.value, (double)ZERO_MAX_SPREAD);
      return;
    case ReplyCode::ERR_ZERO_CAP:
      snprintf(out, n,
               "ERR ZERO offset %.3f mbar exceeds the absolute cap +/-%.1f (ZERO FORCE cannot "
               "override): vessel vented? range jumper on 3000 Pa?",
               (double)r.value, (double)ZERO_FORCE_MAX_ABS_OFFSET);
      return;
    case ReplyCode::ERR_ZERO_OFFSET:
      snprintf(out, n,
               "ERR ZERO offset %.3f mbar exceeds +/-%.1f: vessel vented? range jumper? "
               "(ZERO FORCE overrides up to +/-%.1f)",
               (double)r.value, (double)ZERO_MAX_ABS_OFFSET, (double)ZERO_FORCE_MAX_ABS_OFFSET);
      return;
    case ReplyCode::WARN_ZERO_FORCE:
      snprintf(out, n,
               "WARN ZERO FORCE offset=%.3f mbar exceeds +/-%.1f; saved to NVS, zero_forced=1 "
               "is logged in every sample. START stays refused until a normal ZERO",
               (double)r.value, (double)ZERO_MAX_ABS_OFFSET);
      return;
    case ReplyCode::OK_ZERO:
      snprintf(out, n, "OK ZERO offset=%.3f mbar (saving to NVS)", (double)r.value);
      return;
    case ReplyCode::OK_ZERO_UNSAVED:
      snprintf(out, n, "OK zero marked unsaved: START refused until a ZERO is saved");
      return;
    case ReplyCode::ERR_START_STATE:
      snprintf(out, n, "ERR START only from IDLE, state=%s", st);
      return;
    case ReplyCode::ERR_START_SENSOR:
      snprintf(out, n, "ERR START needs a valid sensor sample");
      return;
    case ReplyCode::ERR_START_NO_ZERO:
      snprintf(out, n, "ERR START refused: %s. Vent the vessel, wait %lu s in IDLE, then ZERO",
               r.flag ? "the active zero was set with ZERO FORCE" : "no valid ZERO saved in NVS",
               (unsigned long)ZERO_MIN_IDLE_S);
      return;
    case ReplyCode::OK_RUN:
      snprintf(out, n, "OK RUN (pump ON, PI active)");
      return;
    case ReplyCode::OK_STOP:
      snprintf(out, n, "OK IDLE (pump OFF, valve OPEN)");
      return;
    case ReplyCode::ERR_STOP_TRIPPED:
      snprintf(out, n, "ERR tripped (%s): STOP does not apply; RESET returns to IDLE",
               tripReasonName(r.reason));
      return;
    case ReplyCode::OK_ALREADY:
      snprintf(out, n, "OK already %s", st);
      return;
    case ReplyCode::ERR_RESET_STATE:
      snprintf(out, n, "ERR not tripped, state=%s", st);
      return;
    case ReplyCode::ERR_RESET_SENSOR:
      snprintf(out, n, "ERR RESET refused: sensor not valid");
      return;
    case ReplyCode::ERR_RESET_PRESSURE:
      snprintf(out, n, "ERR RESET refused: P=%.2f >= %.1f mbar(g)", (double)r.value,
               (double)RESET_MAX_PRESSURE);
      return;
    case ReplyCode::OK_RESET:
      snprintf(out, n, "OK RESET -> IDLE");
      return;
    case ReplyCode::STATUS: {
      const StatusSnapshot &s = r.status;
      snprintf(out, n,
               "state=%s trip=%s P_raw=%.3f P_f=%.3f P_real=%.3f sim_rate=%u V_gpio=%.1fmV valid=%d u_open=%.1f integ=%.3f "
               "pump=%d duty=%.1f warning=%d zero=%.3f%s zero_ok=%d idle_s=%lu latched=%d "
               "auto_clears=%u/%d trip_dec_us=%llu trip_out_us=%llu adc_us=%lu adc_us_max=%lu",
               stateName(s.state), tripReasonName(s.reason), (double)s.p_raw, (double)s.p_f,
               (double)s.p_real, (unsigned)s.sim_rate,
               (double)s.v_gpio_mv, s.valid ? 1 : 0, (double)s.u_open, (double)s.integ,
               s.pump_on ? 1 : 0, (double)s.duty, s.warning ? 1 : 0, (double)s.zero_offset,
               s.zero_forced ? "(FORCED)" : "", s.zero_ok ? 1 : 0, (unsigned long)s.idle_s,
               s.trip_latched ? 1 : 0, (unsigned)s.auto_clears, SOFT_AUTOCLEAR_MAX,
               (unsigned long long)s.trip_decision_us, (unsigned long long)s.trip_output_us,
               (unsigned long)g_adc_read_us, (unsigned long)g_adc_read_us_max);
      return;
    }
    case ReplyCode::ERR_TEST_STATE:
      snprintf(out, n, "ERR TEST only from IDLE, state=%s", st);
      return;
    case ReplyCode::OK_TEST:
      snprintf(out, n,
               "OK TEST (trip limits and open-circuit check still enforced; TEST PUMP ON [<s>] "
               "runs the pump %lu s by default, max %lu s)",
               (unsigned long)TEST_PUMP_DEFAULT_S, (unsigned long)TEST_PUMP_MAX_S);
      return;
    case ReplyCode::ERR_NOT_TEST:
      snprintf(out, n, "ERR not in TEST mode (send TEST first)");
      return;
    case ReplyCode::OK_TEST_SET:
      if (r.flag) {
        snprintf(out, n,
                 "OK TEST pump=ON for %d s (then auto-OFF) valve_duty(energized)=%.0f%%",
                 r.count, (double)r.value);
      } else {
        snprintf(out, n, "OK TEST pump=OFF valve_duty(energized)=%.0f%%", (double)r.value);
      }
      return;
    case ReplyCode::ERR_TEST_DURATION:
      snprintf(out, n, "ERR TEST PUMP ON duration must be 1-%lu s (pump unchanged)",
               (unsigned long)TEST_PUMP_MAX_S);
      return;
    case ReplyCode::OK_TEST_EXIT:
      snprintf(out, n, "OK IDLE");
      return;
    case ReplyCode::ERR_TEST_EXIT:
      snprintf(out, n, "ERR not in TEST mode");
      return;
    case ReplyCode::ERR_SIM_STATE:
      snprintf(out, n, "ERR SIM RAMP only from IDLE or TEST, state=%s", st);
      return;
    case ReplyCode::ERR_SIM_RATE:
      snprintf(out, n, "ERR SIM RAMP rate must be 10, 20 or 30 mbar/min");
      return;
    case ReplyCode::ERR_SIM_SENSOR:
      snprintf(out, n, "ERR SIM RAMP needs a valid sensor sample");
      return;
    case ReplyCode::ERR_SIM_NO_ZERO:
      snprintf(out, n, "ERR SIM RAMP refused: %s. Vent the vessel, wait %lu s in IDLE, then ZERO",
               r.flag ? "zero set with ZERO FORCE" : "no valid ZERO saved in NVS",
               (unsigned long)ZERO_MIN_IDLE_S);
      return;
    case ReplyCode::OK_SIM:
      snprintf(out, n,
               "OK SIM RAMP %d mbar/min from %.2f mbar(g): pump ON, PI drives PCV-101 on the "
               "simulated pressure; REAL 20/29 mbar(g) trips and sensor checks stay active. "
               "SIM EXIT or STOP ends it",
               r.count, (double)r.value);
      return;
    case ReplyCode::OK_SIM_EXIT:
      snprintf(out, n, "OK IDLE (SIM ended: pump OFF, valve released)");
      return;
    case ReplyCode::ERR_SIM_EXIT:
      snprintf(out, n, "ERR not in SIM, state=%s", st);
      return;
    case ReplyCode::ERR_UNKNOWN:
      break;
  }
  snprintf(out, n, "ERR unknown command");
}

static bool transact(Command cmd, Reply *out, bool *timed_out);

// Writes the new zero to NVS and reads it back. On failure the control task is told,
// so START stays refused until a zero is really persisted (review C1).
static void persistZero(const Reply &r) {
  // NVS write happens here (core 0), never in the control task. Only reachable from
  // IDLE (pump OFF, valve OPEN); the flash write stalls both cores briefly (m4).
  bool ok = false;
  Preferences p;
  if (p.begin("m021", false)) {
    const bool w1 = p.putFloat("zero", r.zero_offset) == sizeof(float);
    const bool w2 = p.putBool("zforced", r.zero_forced) > 0;
    const float back = p.getFloat("zero", NAN);
    const bool back_forced = p.getBool("zforced", !r.zero_forced);
    ok = w1 && w2 && back == r.zero_offset && back_forced == r.zero_forced;
    p.end();
  }
  if (ok) {
    Serial.printf("OK zero %.3f mbar saved to NVS%s\n", (double)r.zero_offset,
                  r.zero_forced ? " (forced)" : "");
    return;
  }
  Serial.println(F("WARN could not save the zero offset to NVS; START refused until a ZERO is saved"));
  Command c{CmdType::ZERO_UNSAVED, 0, 0};
  Reply ack;
  bool to = false;
  if (!transact(c, &ack, &to)) Serial.println(F("ERR control task did not acknowledge ZERO_UNSAVED"));
}

static void processReply(const Reply &r, bool late) {
  // Static (serial task only): it is printed before persistZero() can re-enter here.
  static char text[384];  // STATUS is the longest reply (~300 chars with P_real/SIM)
  formatReply(r, text, sizeof(text));
  if (late) Serial.print(F("LATE reply (command was executed): "));
  Serial.println(text);
  if (r.code == ReplyCode::STATUS && !late) {
    Serial.printf("heap_free=%u heap_min=%u stack_free control=%u net=%u serial=%u bytes\n",
                  (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(),
                  g_control_task ? (unsigned)uxTaskGetStackHighWaterMark(g_control_task) : 0,
                  g_net_task ? (unsigned)uxTaskGetStackHighWaterMark(g_net_task) : 0,
                  g_serial_task ? (unsigned)uxTaskGetStackHighWaterMark(g_serial_task) : 0);
  }
  // A late ZERO reply is still persisted (the offset is already active).
  if (r.zero_changed) persistZero(r);
}

// Sends one command and waits for the reply with the same id. Replies to earlier
// commands that arrive meanwhile are printed as LATE (and still processed).
static bool transact(Command cmd, Reply *out, bool *timed_out) {
  cmd.id = g_next_id++;
  if (g_next_id == 0) g_next_id = 1;
  *timed_out = false;
  if (xQueueSend(g_cmd_queue, &cmd, pdMS_TO_TICKS(100)) != pdTRUE) {
    Serial.println(F("ERR control task busy (command not queued, nothing executed)"));
    return false;
  }
  const TickType_t t0 = xTaskGetTickCount();
  const TickType_t limit = pdMS_TO_TICKS(1000);
  for (;;) {
    const TickType_t used = xTaskGetTickCount() - t0;
    if (used >= limit) break;
    Reply r;
    if (xQueueReceive(g_reply_queue, &r, limit - used) != pdTRUE) break;
    if (r.id == cmd.id) {
      *out = r;
      return true;
    }
    processReply(r, true);
  }
  *timed_out = true;
  Serial.println(F("ERR no reply within 1 s: the command may still execute; check STATUS "
                   "(a late reply is printed as LATE)"));
  return false;
}

static void handleLine(char *line) {
  Command cmd{};
  if (!parse(line, &cmd)) {
    Serial.println(F("ERR unknown command"));
    printHelp();
    return;
  }
  Reply r;
  bool timed_out = false;
  if (transact(cmd, &r, &timed_out)) processReply(r, false);
}

void serialTask(void *) {
  char line[64];
  size_t len = 0;
  uint32_t last_print = 0;
  State last_state = State::BOOT;
  TripReason last_reason = TripReason::NONE;
  bool last_pump_timeout = false;
  bool last_latched = false;
  printHelp();

  for (;;) {
    // Late replies (after a timeout) are printed and processed, never dropped.
    Reply late;
    while (xQueueReceive(g_reply_queue, &late, 0) == pdTRUE) processReply(late, true);

    while (Serial.available()) {
      const int ch = Serial.read();
      if (ch == '\r') continue;
      if (ch != '\n') {
        if (len < sizeof(line) - 1) line[len++] = (char)ch;
        continue;
      }
      line[len] = 0;
      len = 0;
      if (line[0]) handleLine(line);
    }

    Sample s;
    if (ring::latest(&s)) {
      // Asynchronous state-change reports (trips in particular).
      // A30: TRIPPED -> RUN only happens through a SOFT_LIMIT auto-clear (RESET goes to
      // IDLE), so this transition is the auto-clear log line, even if a sample was missed.
      if (last_state == State::TRIPPED && s.state == State::RUN) {
        Serial.printf("EVENT AUTO-CLEAR SOFT_LIMIT -> RUN (bumpless PI restart) P_raw=%.3f "
                      "auto_clears=%u/%d in %lu s seq=%lu\n",
                      (double)s.p_raw, (unsigned)s.auto_clears, SOFT_AUTOCLEAR_MAX,
                      (unsigned long)SOFT_AUTOCLEAR_WINDOW_S, (unsigned long)s.seq);
      }
      if (s.state != last_state || s.trip_reason != last_reason || s.trip_latched != last_latched) {
        char how[80] = "";
        if (s.state == State::TRIPPED) {
          if (s.trip_latched) snprintf(how, sizeof(how), " LATCHED (RESET required)");
          else snprintf(how, sizeof(how), " auto-clears at P_raw <= %.1f (RESET -> IDLE instead)",
                        (double)SOFT_AUTOCLEAR_P);
        }
        Serial.printf("EVENT state=%s trip_reason=%s%s P_raw=%.3f V_gpio=%.1fmV auto_clears=%u/%d seq=%lu\n",
                      stateName(s.state), tripReasonName(s.trip_reason), how, (double)s.p_raw,
                      (double)s.v_gpio_mv, (unsigned)s.auto_clears, SOFT_AUTOCLEAR_MAX,
                      (unsigned long)s.seq);
        last_state = s.state;
        last_reason = s.trip_reason;
        last_latched = s.trip_latched;
      }
      if (s.test_pump_timeout && !last_pump_timeout) {
        Serial.printf("EVENT TEST pump OFF: requested duration reached seq=%lu\n",
                      (unsigned long)s.seq);
      }
      last_pump_timeout = s.test_pump_timeout;
      // TEST mode: 5 Hz live print.
      if (s.state == State::TEST && millis() - last_print >= TEST_PRINT_PERIOD_MS) {
        last_print = millis();
        const char *valve = s.valve_duty <= 0.0f    ? "RELEASED(open)"
                            : s.valve_duty >= 100.0f ? "ENERGIZED(closed)"
                                                     : "PWM";
        // Original columns first; the leak model columns (A34, ASSUMED, display only) last.
        Serial.printf("TEST P_raw=%.3f P_f=%.3f V_gpio=%.1fmV valid=%d pump=%s valve=%s duty=%.0f%% "
                      "P_corr=%.3f P_comp_assumed=%.3f leak=%.4f\n",
                      (double)s.p_raw, (double)s.p_f, (double)s.v_gpio_mv, s.valid ? 1 : 0,
                      s.pump_on ? "ON" : "OFF", valve, (double)s.valve_duty, (double)s.p_corr,
                      (double)s.p_comp_assumed, (double)s.leak_rate);
      }
      // SIM RAMP (A35): 5 Hz live print, simulated and REAL pressure side by side.
      if (s.state == State::SIM && millis() - last_print >= TEST_PRINT_PERIOD_MS) {
        last_print = millis();
        Serial.printf("SIM rate=%u P_sim=%.3f P_f=%.3f slope=%.3f warn=%d t_to_limit=%.1f "
                      "P_real=%.3f V_gpio=%.1fmV valid=%d u_open=%.1f duty=%.0f%%\n",
                      (unsigned)s.sim_rate, (double)s.p_raw, (double)s.p_f, (double)s.slope,
                      s.warning ? 1 : 0, (double)s.t_to_limit, (double)s.p_real,
                      (double)s.v_gpio_mv, s.valid ? 1 : 0, (double)s.u_open,
                      (double)s.valve_duty);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
