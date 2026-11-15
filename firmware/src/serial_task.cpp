// Serial task: core 0, low priority. Operator-only commands over USB (115200).
//   ZERO [FORCE] | START | STOP | RESET | STATUS | LED MODE SAMPLE|VENT
//   TEST | TEST PUMP ON|OFF | TEST VALVE ENERGIZE|RELEASE | TEST DUTY <0-100> | TEST EXIT
// Commands are queued to the control task, which owns all state and outputs.
#include <Arduino.h>
#include <Preferences.h>

#include "config.h"
#include "ring.h"
#include "tasks.h"

static void toUpper(char *s) {
  for (; *s; s++) *s = (char)toupper((unsigned char)*s);
}

static bool parse(char *line, Command *cmd) {
  toUpper(line);
  char *t1 = strtok(line, " \t");
  char *t2 = strtok(nullptr, " \t");
  char *t3 = strtok(nullptr, " \t");
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
  if (strcmp(t1, "TEST")) return false;
  if (!t2) { cmd->type = CmdType::TEST_ENTER; return true; }
  if (!strcmp(t2, "EXIT")) { cmd->type = CmdType::TEST_EXIT; return true; }
  if (!t3) return false;
  if (!strcmp(t2, "PUMP")) {
    cmd->type = CmdType::TEST_PUMP;
    if (!strcmp(t3, "ON")) cmd->arg = 1;
    else if (!strcmp(t3, "OFF")) cmd->arg = 0;
    else return false;
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
  Serial.println(F("Commands: ZERO [FORCE], START, STOP, RESET, STATUS, LED MODE SAMPLE|VENT | "
                   "TEST, TEST PUMP ON|OFF, TEST VALVE ENERGIZE|RELEASE, TEST DUTY <0-100>, TEST EXIT"));
}

static void handleLine(char *line) {
  Command cmd;
  if (!parse(line, &cmd)) {
    Serial.println(F("ERR unknown command"));
    printHelp();
    return;
  }
  xQueueReset(g_reply_queue);
  if (xQueueSend(g_cmd_queue, &cmd, pdMS_TO_TICKS(100)) != pdTRUE) {
    Serial.println(F("ERR control task busy"));
    return;
  }
  Reply r;
  if (xQueueReceive(g_reply_queue, &r, pdMS_TO_TICKS(1000)) != pdTRUE) {
    Serial.println(F("ERR no reply from control task"));
    return;
  }
  Serial.println(r.text);
  if (cmd.type == CmdType::STATUS) {
    Serial.printf("heap_free=%u heap_min=%u\n", (unsigned)ESP.getFreeHeap(),
                  (unsigned)ESP.getMinFreeHeap());
  }
  if (r.zero_changed) {
    // NVS write happens here (core 0), never in the control task.
    Preferences p;
    if (p.begin("m021", false)) {
      p.putFloat("zero", r.zero_offset);
      p.putBool("zforced", r.zero_forced);
      p.end();
    } else {
      Serial.println(F("WARN could not open NVS; zero offset not persisted"));
    }
  }
}

void serialTask(void *) {
  char line[64];
  size_t len = 0;
  uint32_t last_print = 0;
  State last_state = State::BOOT;
  TripReason last_reason = TripReason::NONE;
  printHelp();

  for (;;) {
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
      if (s.state != last_state || s.trip_reason != last_reason) {
        Serial.printf("EVENT state=%s trip_reason=%s P_raw=%.3f seq=%lu\n", stateName(s.state),
                      tripReasonName(s.trip_reason), (double)s.p_raw, (unsigned long)s.seq);
        last_state = s.state;
        last_reason = s.trip_reason;
      }
      // TEST mode: 5 Hz live print.
      if (s.state == State::TEST && millis() - last_print >= TEST_PRINT_PERIOD_MS) {
        last_print = millis();
        const char *valve = s.valve_duty <= 0.0f    ? "RELEASED(open)"
                            : s.valve_duty >= 100.0f ? "ENERGIZED(closed)"
                                                     : "PWM";
        Serial.printf("TEST P_raw=%.3f P_f=%.3f V_gpio=%.1fmV valid=%d pump=%s valve=%s duty=%.0f%%\n",
                      (double)s.p_raw, (double)s.p_f, (double)s.v_gpio_mv, s.valid ? 1 : 0,
                      s.pump_on ? "ON" : "OFF", valve, (double)s.valve_duty);
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}
