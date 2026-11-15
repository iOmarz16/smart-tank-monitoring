#include <math.h>
#include <stdio.h>

#include "tasks.h"

static const char *f(char *b, size_t n, float x, int prec) {
  if (isnan(x) || isinf(x)) snprintf(b, n, "null");
  else snprintf(b, n, "%.*f", prec, (double)x);
  return b;
}

static const char *tf(bool b) { return b ? "true" : "false"; }

size_t formatSampleJson(const Sample &s, bool replayed, char *buf, size_t n) {
  char a[16], b[16], c[16], d[16], e[16], g[16], h[16], i[16], j[16];
  int len = snprintf(
      buf, n,
      "{\"type\":\"sample\",\"seq\":%lu,\"t_esp_ms\":%lu,\"P_raw\":%s,\"P_f\":%s,\"valid\":%s,"
      "\"u_open\":%s,\"integ\":%s,\"valve_duty\":%s,\"pump_on\":%s,\"warning\":%s,"
      "\"t_to_limit\":%s,\"state\":\"%s\",\"trip\":%s,\"trip_reason\":\"%s\","
      "\"loop_period_us\":%lu,\"replayed\":%s,\"slope\":%s,\"v_gpio_mv\":%s,"
      "\"sensor_fault\":%s,\"zero_offset\":%s,\"zero_forced\":%s,\"trip_decision_us\":%llu,"
      "\"trip_output_us\":%llu,\"t3_us\":%llu,\"t3_source\":\"%s\",\"led_on\":%s,"
      "\"rb_valve_off\":%s,\"rb_pump_off\":%s,\"reset_reason\":\"%s\"}",
      (unsigned long)s.seq, (unsigned long)s.t_esp_ms, f(a, 16, s.p_raw, 4), f(b, 16, s.p_f, 4),
      tf(s.valid), f(c, 16, s.u_open, 4), f(d, 16, s.integ, 5), f(e, 16, s.valve_duty, 4),
      tf(s.pump_on), tf(s.warning), f(g, 16, s.t_to_limit, 2), stateName(s.state), tf(s.trip),
      tripReasonName(s.trip_reason), (unsigned long)s.loop_period_us, tf(replayed),
      f(h, 16, s.slope, 4), f(i, 16, s.v_gpio_mv, 1), tf(s.sensor_fault), f(j, 16, s.zero_offset, 4),
      tf(s.zero_forced), (unsigned long long)s.trip_decision_us,
      (unsigned long long)s.trip_output_us, (unsigned long long)s.t3_us, t3SourceName(s.t3_source),
      tf(s.led_on), tf(s.rb_valve_off), tf(s.rb_pump_off), resetReasonName(s.reset_reason));
  if (len < 0 || (size_t)len >= n) return 0;
  return (size_t)len;
}
