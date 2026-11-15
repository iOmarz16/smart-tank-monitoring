#include "ring.h"

#include <Arduino.h>
#include <stdlib.h>

#include "config.h"

namespace ring {

static Sample *g_buf = nullptr;
static uint32_t g_count = 0;   // total samples ever pushed
static portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;

bool init() {
  g_buf = (Sample *)calloc(RING_SAMPLES, sizeof(Sample));
  return g_buf != nullptr;
}

void push(const Sample &s) {
  portENTER_CRITICAL(&g_mux);
  g_buf[s.seq % RING_SAMPLES] = s;
  g_count = s.seq + 1;
  portEXIT_CRITICAL(&g_mux);
}

static bool rangeLocked(uint32_t *oldest, uint32_t *newest) {
  if (g_count == 0) return false;
  *newest = g_count - 1;
  *oldest = g_count > RING_SAMPLES ? g_count - RING_SAMPLES : 0;
  return true;
}

bool range(uint32_t *oldest, uint32_t *newest) {
  portENTER_CRITICAL(&g_mux);
  const bool ok = rangeLocked(oldest, newest);
  portEXIT_CRITICAL(&g_mux);
  return ok;
}

bool get(uint32_t seq, Sample *out) {
  bool ok = false;
  portENTER_CRITICAL(&g_mux);
  uint32_t o, n;
  if (rangeLocked(&o, &n) && seq >= o && seq <= n) {
    *out = g_buf[seq % RING_SAMPLES];
    ok = true;
  }
  portEXIT_CRITICAL(&g_mux);
  return ok;
}

bool latest(Sample *out) {
  bool ok = false;
  portENTER_CRITICAL(&g_mux);
  if (g_count > 0) {
    *out = g_buf[(g_count - 1) % RING_SAMPLES];
    ok = true;
  }
  portEXIT_CRITICAL(&g_mux);
  return ok;
}

}  // namespace ring
