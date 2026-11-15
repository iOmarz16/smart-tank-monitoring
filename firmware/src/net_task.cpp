// Network task: core 0. Wi-Fi access point + WebSocket server.
// Pushes one JSON object per sample from the ring buffer. After a (re)connect
// the whole buffer is replayed with replayed=true; the dashboard deduplicates
// by seq, so an outage shorter than the buffer leaves no gap (test I2).
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
  uint32_t next;          // next seq to send
  uint32_t replay_until;  // seqs <= this were buffered before the connect
};
static ClientCursor g_clients[WEBSOCKETS_SERVER_CLIENT_MAX];

static void onEvent(uint8_t num, WStype_t type, uint8_t *payload, size_t length) {
  if (num >= WEBSOCKETS_SERVER_CLIENT_MAX) return;
  switch (type) {
    case WStype_CONNECTED: {
      uint32_t oldest = 0, newest = 0;
      ClientCursor &c = g_clients[num];
      if (ring::range(&oldest, &newest)) {
        c.next = oldest;
        c.replay_until = newest;
      } else {
        c.next = 0;
        c.replay_until = 0;
      }
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

void netTask(void *) {
  startAccessPoint();
  g_ws.begin();
  g_ws.onEvent(onEvent);

  static char buf[640];
  for (;;) {
    g_ws.loop();
    uint32_t oldest = 0, newest = 0;
    if (ring::range(&oldest, &newest)) {
      for (int k = 0; k < WEBSOCKETS_SERVER_CLIENT_MAX; k++) {
        ClientCursor &c = g_clients[k];
        int sent = 0;
        while (c.active && c.next <= newest && sent < NET_SEND_BURST) {
          if (c.next < oldest) c.next = oldest;  // overwritten: older than the buffer
          Sample s;
          if (!ring::get(c.next, &s)) break;
          const bool replayed =
              (c.next <= c.replay_until) || (newest - c.next >= REPLAY_LAG_SAMPLES);
          const size_t len = formatSampleJson(s, replayed, buf, sizeof(buf));
          if (len == 0) {
            c.next++;  // cannot happen with the fixed format; skip rather than stall
            continue;
          }
          if (!g_ws.sendTXT((uint8_t)k, buf, len)) break;  // disconnect event clears active
          c.next++;
          sent++;
        }
      }
    }
    vTaskDelay(1);  // 1 ms tick: yield every pass so core-0 idle can feed its watchdog
  }
}
