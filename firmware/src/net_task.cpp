// Network task: core 0. Wi-Fi access point + WebSocket server.
// Pushes one JSON object per sample from the ring buffer. After a (re)connect
// the whole buffer is replayed with replayed=true; the dashboard deduplicates
// by seq, so an outage shorter than the buffer leaves no gap (test I2).
// Live samples are always sent first; the backlog is interleaved at
// REPLAY_RATE_HZ, so a live sample never waits behind it (Spec 4 latency).
// The ONLY inbound message honoured is {"type":"ping","t_pc":...}.
// No Internet, DNS, NTP or external host (Constraint 4).
#include <Arduino.h>
#include <ArduinoJson.h>
#include <WebSocketsServer.h>
#include <WiFi.h>
#include <esp_timer.h>
#include <esp_wifi.h>

#include "config.h"
#include "ring.h"
#include "tasks.h"

static WebSocketsServer g_ws(WS_PORT);

struct ClientCursor {
  bool active;
  uint32_t live_next;    // next live seq (samples produced after the connect)
  bool replaying;        // backlog not yet fully sent
  uint32_t replay_next;  // next backlog seq
  uint32_t replay_end;   // last backlog seq (newest at connect time)
  float tokens;          // replay token bucket
  uint32_t last_us;
};
static ClientCursor g_clients[WEBSOCKETS_SERVER_CLIENT_MAX];

static void onEvent(uint8_t num, WStype_t type, uint8_t *payload, size_t length) {
  if (num >= WEBSOCKETS_SERVER_CLIENT_MAX) return;
  switch (type) {
    case WStype_CONNECTED: {
      uint32_t oldest = 0, newest = 0;
      ClientCursor &c = g_clients[num];
      if (ring::range(&oldest, &newest)) {
        c.live_next = newest + 1;
        c.replaying = true;
        c.replay_next = oldest;
        c.replay_end = newest;
      } else {
        c.live_next = 0;
        c.replaying = false;
      }
      c.tokens = 0.0f;
      c.last_us = micros();
      c.active = true;
      break;
    }
    case WStype_DISCONNECTED:
      g_clients[num].active = false;
      break;
    case WStype_TEXT: {
      JsonDocument doc;
      if (deserializeJson(doc, payload, length)) return;  // ignore malformed
      if (doc["type"] != "ping" || !doc["t_pc"].is<double>()) return;  // ignore everything else
      const double t_esp = (double)esp_timer_get_time() / 1000.0;
      char out[128];
      snprintf(out, sizeof(out), "{\"type\":\"pong\",\"t_pc\":%.6f,\"t_esp\":%.3f}",
               doc["t_pc"].as<double>(), t_esp);
      g_ws.sendTXT(num, out);
      break;
    }
    default:
      break;  // binary, fragments, protocol pings: ignored
  }
}

static void startAccessPoint() {
  WiFi.persistent(false);  // never write Wi-Fi settings to flash
  WiFi.mode(WIFI_AP);
  const IPAddress ip(WIFI_AP_IP[0], WIFI_AP_IP[1], WIFI_AP_IP[2], WIFI_AP_IP[3]);
  WiFi.softAPConfig(ip, ip, IPAddress(255, 255, 255, 0));
  WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD, WIFI_AP_CHANNEL, 0, WIFI_AP_MAX_CLIENTS);
  esp_wifi_set_ps(WIFI_PS_NONE);  // lowest latency (Spec 4)
}

// Returns false if the sample could not be sent (client gone); true otherwise
// (a sample missing from the ring or failing to format is skipped).
static bool sendSample(uint8_t client, uint32_t seq, bool replayed) {
  static char buf[1024];  // worst-case sample ~860 bytes (with the A34 leak and A35 SIM fields)
  Sample s;
  if (!ring::get(seq, &s)) return true;
  const size_t len = formatSampleJson(s, replayed, buf, sizeof(buf));
  if (len == 0) return true;  // cannot happen with the fixed format; skip rather than stall
  return g_ws.sendTXT(client, buf, len);  // false -> disconnect event clears active
}

void netTask(void *) {
  printHeap("before Wi-Fi");
  startAccessPoint();
  g_ws.begin();
  g_ws.onEvent(onEvent);
  // A33: at least HEAP_MIN_FREE_AFTER_WIFI must remain with Wi-Fi and WebSocket up.
  const size_t free_after = printHeap("after Wi-Fi + WebSocket");
  if (free_after < HEAP_MIN_FREE_AFTER_WIFI) {
    Serial.printf("WARNING free heap %u < %u bytes after Wi-Fi (A33): report this, do not run tests\n",
                  (unsigned)free_after, (unsigned)HEAP_MIN_FREE_AFTER_WIFI);
  }

  for (;;) {
    g_ws.loop();
    uint32_t oldest = 0, newest = 0;
    if (ring::range(&oldest, &newest)) {
      for (int k = 0; k < WEBSOCKETS_SERVER_CLIENT_MAX; k++) {
        ClientCursor &c = g_clients[k];
        if (!c.active) continue;
        // 1) live samples first, always
        int sent = 0;
        while (c.active && c.live_next <= newest && sent < NET_SEND_BURST) {
          if (c.live_next < oldest) c.live_next = oldest;  // overwritten (should not happen)
          const bool late = newest - c.live_next >= REPLAY_LAG_SAMPLES;
          if (!sendSample((uint8_t)k, c.live_next, late)) break;
          c.live_next++;
          sent++;
        }
        // 2) then the backlog, rate-limited (token bucket)
        const uint32_t now = micros();
        c.tokens += (float)(now - c.last_us) * (float)REPLAY_RATE_HZ / 1e6f;
        if (c.tokens > 2.0f) c.tokens = 2.0f;  // small burst only
        c.last_us = now;
        while (c.active && c.replaying && c.tokens >= 1.0f) {
          if (c.replay_next < oldest) c.replay_next = oldest;  // overwritten during the outage
          if (c.replay_next > c.replay_end) {
            c.replaying = false;
            break;
          }
          if (!sendSample((uint8_t)k, c.replay_next, true)) break;
          c.replay_next++;
          c.tokens -= 1.0f;
        }
      }
    }
    vTaskDelay(1);  // 1 ms tick: yield every pass so core-0 idle can feed its watchdog
  }
}
