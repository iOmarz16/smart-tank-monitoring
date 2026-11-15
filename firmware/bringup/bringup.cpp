// TEAM M021 - minimal hardware bring-up firmware (PlatformIO env "bringup").
// NOT the controller: no PI, no Wi-Fi, no ring buffer, no NVS, no sensor-loss detection.
// Use only attended, for wiring checks. Pins come from include/config.h (same as the main
// firmware): GPIO34 PT-101 (via the /5 module), GPIO26 PCV-101 valve, GPIO27 P-101 pump.
//
// Serial (115200, end each command with Enter):
//   p<N>  pump ON for N seconds (1..30), then OFF automatically
//   v1    valve energize (closed)      v0  valve release (open)
//   s     stop: pump OFF, valve released
//   r     clear a TRIP (only if P_mbar < 20)
// Safety: P_mbar >= 20 -> pump OFF + valve released in the same loop, TRIP printed, and
// p / v1 are refused until r.
#include <Arduino.h>

#include "config.h"

static constexpr uint32_t PRINT_PERIOD_MS = 200;
static constexpr uint32_t CHECK_PERIOD_MS = 50;   // read + trip check rate (4x the print rate)
static constexpr int READS = 32;
static constexpr float MBAR_PER_MV = 0.015f;       // /5 module, 0-10 V = 0-30 mbar(g)
static constexpr float TRIP_MBAR = 20.0f;
static constexpr uint32_t PUMP_MAX_S = 30;

static bool g_pump = false;
static bool g_valve = false;  // true = energized = CLOSED
static bool g_tripped = false;
static uint32_t g_pump_until = 0;
static float g_mv = 0.0f;
static float g_p = 0.0f;

static void writeOutputs() {
  digitalWrite(PIN_PUMP, g_pump ? HIGH : LOW);
  digitalWrite(PIN_VALVE, g_valve ? HIGH : LOW);
}

static void safeOff() {
  g_pump = false;
  g_valve = false;
  writeOutputs();
}

static void readSensor() {
  uint32_t sum = 0;
  for (int i = 0; i < READS; i++) sum += analogReadMilliVolts(PIN_PT101);
  g_mv = (float)sum / (float)READS;
  g_p = g_mv * MBAR_PER_MV;
}

static void handleCommand(char *cmd) {
  if (!strcmp(cmd, "s")) {
    safeOff();
    Serial.println("OK stop: pump OFF, valve released");
  } else if (!strcmp(cmd, "v0")) {
    g_valve = false;
    writeOutputs();
    Serial.println("OK valve released (open)");
  } else if (!strcmp(cmd, "v1")) {
    if (g_tripped) { Serial.println("ERR tripped: v1 refused, type r"); return; }
    g_valve = true;
    writeOutputs();
    Serial.println("OK valve energized (closed)");
  } else if (cmd[0] == 'p') {
    if (g_tripped) { Serial.println("ERR tripped: p refused, type r"); return; }
    char *end = nullptr;
    const long n = strtol(cmd + 1, &end, 10);
    if (cmd[1] == 0 || !end || *end || n < 1 || n > (long)PUMP_MAX_S) {
      Serial.printf("ERR use p<N> with N = 1..%lu seconds\n", (unsigned long)PUMP_MAX_S);
      return;
    }
    g_pump = true;
    g_pump_until = millis() + (uint32_t)n * 1000UL;  // a new p replaces the remaining time
    writeOutputs();
    Serial.printf("OK pump ON for %ld s\n", n);
  } else if (!strcmp(cmd, "r")) {
    if (!g_tripped) { Serial.println("OK not tripped"); return; }
    if (g_p >= TRIP_MBAR) {
      Serial.printf("ERR r refused: P_mbar=%.2f >= %.1f\n", (double)g_p, (double)TRIP_MBAR);
      return;
    }
    g_tripped = false;
    Serial.println("OK trip cleared (pump OFF, valve released)");
  } else {
    Serial.println("ERR commands: p<1-30>, v1, v0, s, r");
  }
}

void setup() {
  // First thing: both outputs driven LOW (pump OFF, valve released/open).
  digitalWrite(PIN_VALVE, LOW);
  digitalWrite(PIN_PUMP, LOW);
  pinMode(PIN_VALVE, OUTPUT);
  pinMode(PIN_PUMP, OUTPUT);
  digitalWrite(PIN_VALVE, LOW);
  digitalWrite(PIN_PUMP, LOW);

  Serial.begin(SERIAL_BAUD);
  analogSetAttenuation(ADC_11db);  // global (used by analogReadMilliVolts) and per pin
  analogSetPinAttenuation(PIN_PT101, ADC_11db);
  delay(200);
  Serial.println();
  Serial.println("M021 BRING-UP firmware (not the controller). Commands: p<1-30>, v1, v0, s, r");
  Serial.println("No sensor-loss detection: a broken PT-101 wire reads ~0 mbar. Stay attended.");
}

void loop() {
  static uint32_t last_check = 0, last_print = 0;
  static char line[16];
  static size_t len = 0;

  while (Serial.available()) {
    const int ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      line[len] = 0;
      len = 0;
      if (line[0]) handleCommand(line);
    } else if (len < sizeof(line) - 1) {
      line[len++] = (char)tolower(ch);
    }
  }

  const uint32_t now = millis();
  if (now - last_check < CHECK_PERIOD_MS) return;
  last_check = now;

  readSensor();
  if (g_p >= TRIP_MBAR) {
    // Same loop as the reading: outputs safe first, then report.
    const bool was_tripped = g_tripped;
    safeOff();
    g_tripped = true;
    if (!was_tripped) Serial.printf("TRIP P_mbar=%.2f >= %.1f: pump OFF, valve released. Type r when P < 20\n",
                                    (double)g_p, (double)TRIP_MBAR);
  }
  if (g_pump && (int32_t)(now - g_pump_until) >= 0) {
    g_pump = false;
    writeOutputs();
    Serial.println("EVENT pump OFF: time reached");
  }

  if (now - last_print >= PRINT_PERIOD_MS) {
    last_print = now;
    const long left = g_pump ? (long)((g_pump_until - now + 999) / 1000) : 0;
    Serial.printf("t=%lu v_gpio_mv=%.1f P_mbar=%.3f pump=%s", (unsigned long)now, (double)g_mv,
                  (double)g_p, g_pump ? "ON" : "OFF");
    if (g_pump) Serial.printf("(%lds left)", left);
    Serial.printf(" valve=%s%s\n", g_valve ? "ENERGIZED(closed)" : "RELEASED(open)",
                  g_tripped ? " TRIPPED" : "");
  }
}
