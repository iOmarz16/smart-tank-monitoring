#include "ring.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "config.h"
#include "leak_model.h"  // leak::rate() only, to rebuild the logged leak_rate

// Memory fix (A33). The first version held 1500 full Sample structs (96 B) in ONE
// calloc of 144 KB, larger than the largest free block of the ESP32 heap (~110 KB), so
// it failed on the real board. Now:
//   - each sample is stored packed in 48 B (scaled integers, bitfields; seq is implicit;
//     44 B before the leak model fields, A34)
//   - the buffer is RING_SAMPLES / RING_CHUNK_SAMPLES separate allocations
// 1500 x 48 B = 72 KB in 10 blocks of 7.2 KB. The control logic never reads the ring:
// packing only quantizes the LOGGED values (see the scales below and docs/protocol.md).

namespace ring {

namespace {

// Scales of the stored values (value = stored / scale).
constexpr float SC_MV = 10.0f;      // v_gpio_mv: 0.1 mV, 0..6553.5
constexpr float SC_P = 500.0f;      // p_raw, p_f: 0.002 mbar, +/-65.5 mbar(g)
constexpr float SC_SLOPE = 1000.0f; // slope: 0.001 mbar/s, +/-32.7
constexpr float SC_TTL = 50.0f;     // t_to_limit: 0.02 s, +/-655 s
constexpr float SC_PCT = 100.0f;    // u_open, valve_duty: 0.01 %, 0..655 %
constexpr float SC_ZERO = 5000.0f;  // zero_offset: 0.0002 mbar, +/-6.55 (cap is 5)
// Leak model (A34), log only. P_comp grows without bound while the pump runs with the
// vent closed, so it gets a wide range: 0.02 mbar, +/-655 mbar (saturates beyond).
constexpr float SC_PCOMP = 50.0f;
// P_corr: same scale as P_raw (0.002 mbar). leak_rate is not stored (rebuilt, see Packed).
constexpr int16_t S16_NAN = INT16_MIN;

struct Packed {
  uint32_t t_esp_ms;
  uint32_t loop_period_us;
  float integ;                // kept as float: the I5 recomputation uses it directly
  uint32_t trip_dec_lo;       // trip_decision_us, low 32 bits
  uint32_t t3_delta1;         // t3_us - trip_decision_us + 1; 0 = t3_us is 0
  uint32_t state : 3;
  uint32_t trip_reason : 2;
  uint32_t t3_source : 2;
  uint32_t valid : 1;
  uint32_t sensor_fault : 1;
  uint32_t pump_on : 1;
  uint32_t warning : 1;
  uint32_t trip : 1;
  uint32_t led_on : 1;
  uint32_t zero_forced : 1;
  uint32_t rb_valve_off : 1;
  uint32_t rb_pump_off : 1;
  uint32_t test_pump_timeout : 1;
  uint32_t trip_latched : 1;
  uint32_t auto_clear_event : 1;
  uint32_t leak_pump_on : 1;  // leak model pump state (selects k for the rebuilt leak_rate)
  uint32_t auto_clears : 4;
  uint32_t reset_reason : 5;
  uint32_t simulated : 1;     // A35: control pressure was the SIM RAMP pressure
  uint32_t sim_rate : 2;      // A35: SIM RAMP rate / 10 (1, 2, 3), 0 = not simulated
  uint16_t v_gpio;
  int16_t p_raw;
  int16_t p_f;
  int16_t slope;
  int16_t t_to_limit;
  uint16_t u_open;
  uint16_t valve_duty;
  int16_t zero_offset;
  uint16_t trip_dec_hi;       // trip_decision_us, bits 32..47 (8.9 years of uptime)
  uint16_t out_delta1;        // trip_output_us - trip_decision_us + 1 (saturates); 0 = none
  // Leak model (A34). leak_rate is not stored: it is k(leak_pump_on) * sqrt(P_corr),
  // rebuilt from P_corr (A35: P_corr is stored because in SIM it follows the REAL P_f,
  // not the logged P_f). P_real is not stored either: in SIM it is rebuilt exactly as
  // the supervisor computes it, v_gpio_mv x 0.015 - zero_offset; outside SIM = P_raw.
  int16_t p_comp;
  int16_t p_corr;
};
static_assert(sizeof(Packed) == 48, "packed sample layout changed");
static_assert(RING_SAMPLES % RING_CHUNK_SAMPLES == 0, "RING_SAMPLES must be a multiple of the chunk");
static_assert(RING_SAMPLES >= 900, "ring must hold >= 900 samples (60 s outage + margin): ask first");
static_assert((int)State::SIM < 8 && (int)TripReason::HARD_LIMIT < 4 &&
                  (int)T3Source::READBACK_TIMEOUT < 4,
              "enum does not fit its bitfield");
static_assert(SOFT_AUTOCLEAR_MAX < 16, "auto_clears does not fit its bitfield");
// The full 32-bit flag word is used; read_ok is always true on the ESP32 (P_real rebuild).

constexpr uint32_t N_CHUNKS = RING_SAMPLES / RING_CHUNK_SAMPLES;

Packed *g_chunks[N_CHUNKS] = {nullptr};
uint32_t g_count = 0;  // total samples ever pushed
portMUX_TYPE g_mux = portMUX_INITIALIZER_UNLOCKED;

int16_t encS16(float x, float scale) {
  if (isnan(x)) return S16_NAN;
  float v = roundf(x * scale);
  if (v > 32767.0f) v = 32767.0f;
  if (v < -32767.0f) v = -32767.0f;  // -32768 is the NaN marker
  return (int16_t)v;
}
float decS16(int16_t v, float scale) { return v == S16_NAN ? NAN : (float)v / scale; }

uint16_t encU16(float x, float scale) {
  if (isnan(x)) return 0;
  float v = roundf(x * scale);
  if (v > 65535.0f) v = 65535.0f;
  if (v < 0.0f) v = 0.0f;
  return (uint16_t)v;
}
float decU16(uint16_t v, float scale) { return (float)v / scale; }

void pack(const Sample &s, Packed *p) {
  p->t_esp_ms = s.t_esp_ms;
  p->loop_period_us = s.loop_period_us;
  p->integ = s.integ;
  p->trip_dec_lo = (uint32_t)(s.trip_decision_us & 0xFFFFFFFFULL);
  p->trip_dec_hi = (uint16_t)((s.trip_decision_us >> 32) & 0xFFFFULL);
  if (s.trip_output_us == 0 || s.trip_output_us < s.trip_decision_us) {
    p->out_delta1 = 0;
  } else {
    const uint64_t d = s.trip_output_us - s.trip_decision_us + 1;
    p->out_delta1 = d > 0xFFFFULL ? 0xFFFF : (uint16_t)d;
  }
  if (s.t3_us == 0 || s.t3_us < s.trip_decision_us) {
    p->t3_delta1 = 0;
  } else {
    const uint64_t d = s.t3_us - s.trip_decision_us + 1;
    p->t3_delta1 = d > 0xFFFFFFFFULL ? 0xFFFFFFFFUL : (uint32_t)d;
  }
  p->state = (uint32_t)s.state;
  p->trip_reason = (uint32_t)s.trip_reason;
  p->t3_source = (uint32_t)s.t3_source;
  p->valid = s.valid;
  p->sensor_fault = s.sensor_fault;
  p->pump_on = s.pump_on;
  p->warning = s.warning;
  p->trip = s.trip;
  p->led_on = s.led_on;
  p->zero_forced = s.zero_forced;
  p->rb_valve_off = s.rb_valve_off;
  p->rb_pump_off = s.rb_pump_off;
  p->test_pump_timeout = s.test_pump_timeout;
  p->trip_latched = s.trip_latched;
  p->auto_clear_event = s.auto_clear_event;
  p->auto_clears = s.auto_clears > 15 ? 15 : s.auto_clears;
  p->reset_reason = s.reset_reason > 31 ? 31 : s.reset_reason;
  p->v_gpio = encU16(s.v_gpio_mv, SC_MV);
  p->p_raw = encS16(s.p_raw, SC_P);
  p->p_f = encS16(s.p_f, SC_P);
  p->slope = encS16(s.slope, SC_SLOPE);
  p->t_to_limit = encS16(s.t_to_limit, SC_TTL);
  p->u_open = encU16(s.u_open, SC_PCT);
  p->valve_duty = encU16(s.valve_duty, SC_PCT);
  p->zero_offset = encS16(s.zero_offset, SC_ZERO);
  p->leak_pump_on = s.leak_pump_on;
  p->p_comp = encS16(s.p_comp_assumed, SC_PCOMP);
  p->p_corr = encS16(s.p_corr, SC_P);
  p->simulated = s.simulated;
  p->sim_rate = s.simulated ? (s.sim_rate / 10) & 3 : 0;
}

void unpack(const Packed &p, uint32_t seq, Sample *s) {
  memset(s, 0, sizeof(*s));
  s->seq = seq;
  s->t_esp_ms = p.t_esp_ms;
  s->loop_period_us = p.loop_period_us;
  s->integ = p.integ;
  s->trip_decision_us = ((uint64_t)p.trip_dec_hi << 32) | p.trip_dec_lo;
  s->trip_output_us = p.out_delta1 ? s->trip_decision_us + p.out_delta1 - 1 : 0;
  s->t3_us = p.t3_delta1 ? s->trip_decision_us + p.t3_delta1 - 1 : 0;
  s->state = (State)p.state;
  s->trip_reason = (TripReason)p.trip_reason;
  s->t3_source = (T3Source)p.t3_source;
  s->valid = p.valid;
  s->sensor_fault = p.sensor_fault;
  s->pump_on = p.pump_on;
  s->warning = p.warning;
  s->trip = p.trip;
  s->led_on = p.led_on;
  s->zero_forced = p.zero_forced;
  s->rb_valve_off = p.rb_valve_off;
  s->rb_pump_off = p.rb_pump_off;
  s->test_pump_timeout = p.test_pump_timeout;
  s->trip_latched = p.trip_latched;
  s->auto_clear_event = p.auto_clear_event;
  s->auto_clears = (uint8_t)p.auto_clears;
  s->reset_reason = (uint8_t)p.reset_reason;
  s->v_gpio_mv = decU16(p.v_gpio, SC_MV);
  s->p_raw = decS16(p.p_raw, SC_P);
  s->p_f = decS16(p.p_f, SC_P);
  s->slope = decS16(p.slope, SC_SLOPE);
  s->t_to_limit = decS16(p.t_to_limit, SC_TTL);
  s->u_open = decU16(p.u_open, SC_PCT);
  s->valve_duty = decU16(p.valve_duty, SC_PCT);
  s->zero_offset = decS16(p.zero_offset, SC_ZERO);
  s->leak_pump_on = p.leak_pump_on;
  s->p_corr = decS16(p.p_corr, SC_P);
  s->p_comp_assumed = decS16(p.p_comp, SC_PCOMP);
  s->leak_rate = leak::rate(s->p_corr, p.leak_pump_on);
  s->simulated = p.simulated;
  s->sim_rate = (uint8_t)(p.sim_rate * 10);
  // P_real: outside SIM it is P_raw; in SIM, the supervisor formula on the stored pin
  // voltage and zero offset (0.0015 mbar resolution). read_ok is always true here.
  s->p_real = p.simulated ? s->v_gpio_mv * (SENSOR_DIVIDER_RATIO * SENSOR_MBAR_PER_V / 1000.0f) - s->zero_offset
                          : s->p_raw;
  s->p_f_real = NAN;  // internal, not logged
}

Packed *slot(uint32_t seq) {
  const uint32_t i = seq % RING_SAMPLES;
  return &g_chunks[i / RING_CHUNK_SAMPLES][i % RING_CHUNK_SAMPLES];
}

bool near(float a, float b, float tol) {
  if (isnan(a) || isnan(b)) return isnan(a) && isnan(b);
  return fabsf(a - b) <= tol;
}

}  // namespace

bool init() {
  for (uint32_t c = 0; c < N_CHUNKS; c++) {
    g_chunks[c] = (Packed *)heap_caps_calloc(RING_CHUNK_SAMPLES, sizeof(Packed),
                                             MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!g_chunks[c]) {
      for (uint32_t k = 0; k < c; k++) {
        heap_caps_free(g_chunks[k]);
        g_chunks[k] = nullptr;
      }
      return false;
    }
  }
  return true;
}

size_t bytes() { return (size_t)RING_SAMPLES * sizeof(Packed); }

bool selfTest() {
  // Round trip of representative and edge values through pack/unpack (A33). It runs
  // on the target at boot, because no host compiler is available for a unit test.
  Sample a{};
  a.seq = 7;
  a.t_esp_ms = 123456789UL;
  a.loop_period_us = 100004;
  a.v_gpio_mv = 1333.37f;
  a.p_raw = 19.9993f;
  a.p_f = NAN;
  a.slope = -0.1234f;
  a.t_to_limit = 12.345f;
  a.u_open = 37.256f;
  a.integ = 123.45678f;
  a.valve_duty = 62.744f;
  a.zero_offset = -4.9998f;
  a.trip_decision_us = 0x123456789ABULL;
  a.trip_output_us = a.trip_decision_us + 37;
  a.t3_us = a.trip_decision_us + 912345;
  a.state = State::TRIPPED;
  a.trip_reason = TripReason::HARD_LIMIT;
  a.t3_source = T3Source::READBACK_TIMEOUT;
  a.valid = a.pump_on = a.trip = a.zero_forced = a.rb_pump_off = a.trip_latched = true;
  a.auto_clears = 3;
  a.reset_reason = 10;
  a.leak_pump_on = true;
  a.p_corr = NAN;  // P_f is NaN here
  a.p_comp_assumed = 123.456f;
  a.leak_rate = NAN;
  Packed p{};
  pack(a, &p);
  Sample b;
  unpack(p, a.seq, &b);
  bool ok = b.seq == a.seq && b.t_esp_ms == a.t_esp_ms && b.loop_period_us == a.loop_period_us &&
            b.integ == a.integ && b.trip_decision_us == a.trip_decision_us &&
            b.trip_output_us == a.trip_output_us && b.t3_us == a.t3_us && b.state == a.state &&
            b.trip_reason == a.trip_reason && b.t3_source == a.t3_source && b.valid &&
            !b.sensor_fault && b.pump_on && !b.warning && b.trip && !b.led_on && b.zero_forced &&
            !b.rb_valve_off && b.rb_pump_off && !b.test_pump_timeout && b.trip_latched &&
            !b.auto_clear_event && b.auto_clears == 3 && b.reset_reason == 10 &&
            near(b.v_gpio_mv, a.v_gpio_mv, 0.05f) && near(b.p_raw, a.p_raw, 0.001f) &&
            near(b.p_f, a.p_f, 0.0f) && near(b.slope, a.slope, 0.0005f) &&
            near(b.t_to_limit, a.t_to_limit, 0.01f) && near(b.u_open, a.u_open, 0.005f) &&
            near(b.valve_duty, a.valve_duty, 0.005f) && near(b.zero_offset, a.zero_offset, 0.0001f) &&
            near(b.p_corr, a.p_corr, 0.0f) && near(b.p_comp_assumed, a.p_comp_assumed, 0.01f) &&
            near(b.leak_rate, a.leak_rate, 0.0f) && b.leak_pump_on && !b.simulated &&
            b.sim_rate == 0 && near(b.p_real, b.p_raw, 0.0f);
  // Leak model (A34): P_corr stored, leak_rate rebuilt with the k of the pump bit.
  Sample d{};
  d.p_f = 5.0f;
  d.leak_pump_on = true;
  d.p_corr = d.p_f + PUMP_ON_OFFSET;
  d.p_comp_assumed = NAN;
  d.leak_rate = K_LEAK_PUMP_ON * sqrtf(d.p_corr);
  pack(d, &p);
  unpack(p, 0, &b);
  ok = ok && b.leak_pump_on && near(b.p_corr, d.p_corr, 0.001f) && isnan(b.p_comp_assumed) &&
       near(b.leak_rate, d.leak_rate, 0.0001f);
  d.leak_pump_on = false;
  d.p_corr = d.p_f;
  d.leak_rate = K_LEAK_PUMP_OFF * sqrtf(d.p_corr);
  d.p_comp_assumed = 999.0f;  // saturates at 32767 / SC_PCOMP = 655.34
  pack(d, &p);
  unpack(p, 0, &b);
  ok = ok && !b.leak_pump_on && near(b.p_corr, 5.0f, 0.001f) &&
       near(b.leak_rate, d.leak_rate, 0.0001f) && near(b.p_comp_assumed, 32767.0f / SC_PCOMP, 0.0f);
  // SIM (A35): control pressure simulated, P_real rebuilt from the pin voltage and zero.
  Sample e{};
  e.state = State::SIM;
  e.simulated = true;
  e.sim_rate = 30;
  e.p_raw = 17.25f;                  // simulated
  e.v_gpio_mv = 314.0f;              // real pin voltage
  e.zero_offset = 2.13f;
  e.p_real = 314.0f * 0.015f - 2.13f;  // 2.58 mbar(g), as the supervisor computes it
  pack(e, &p);
  unpack(p, 0, &b);
  ok = ok && b.state == State::SIM && b.simulated && b.sim_rate == 30 &&
       near(b.p_raw, 17.25f, 0.001f) && near(b.p_real, e.p_real, 0.002f);
  // No trip: all trip times 0. Out of range values saturate instead of wrapping.
  Sample c{};
  c.p_raw = 99.0f;
  c.p_f = -99.0f;
  c.u_open = 100.0f;
  c.t_to_limit = NAN;
  pack(c, &p);
  unpack(p, 0, &b);
  ok = ok && b.trip_decision_us == 0 && b.trip_output_us == 0 && b.t3_us == 0 &&
       near(b.p_raw, 32767.0f / SC_P, 0.0f) && near(b.p_f, -32767.0f / SC_P, 0.0f) &&
       near(b.u_open, 100.0f, 0.0f) && isnan(b.t_to_limit);
  return ok;
}

void push(const Sample &s) {
  Packed p{};
  pack(s, &p);  // outside the critical section
  portENTER_CRITICAL(&g_mux);
  *slot(s.seq) = p;
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
  Packed p{};
  bool ok = false;
  portENTER_CRITICAL(&g_mux);
  uint32_t o, n;
  if (rangeLocked(&o, &n) && seq >= o && seq <= n) {
    p = *slot(seq);
    ok = true;
  }
  portEXIT_CRITICAL(&g_mux);
  if (ok) unpack(p, seq, out);
  return ok;
}

bool latest(Sample *out) {
  Packed p{};
  uint32_t seq = 0;
  bool ok = false;
  portENTER_CRITICAL(&g_mux);
  if (g_count > 0) {
    seq = g_count - 1;
    p = *slot(seq);
    ok = true;
  }
  portEXIT_CRITICAL(&g_mux);
  if (ok) unpack(p, seq, out);
  return ok;
}

}  // namespace ring
