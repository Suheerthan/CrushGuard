/*
  CrushGuard gateway (ESP32 plugged into the control-room laptop by USB)
  ----------------------------------------------------------------------
  ESP-NOW telemetry from nodes  ->  one JSON line per packet on USB serial.
  Text commands from the server ->  ESP-NOW command broadcast to the nodes.

  Serial commands (from server/serial_bridge.py, 921600 baud):
    L <node|0> <level 0-2> <hold_s>   force beacon level (server decision)
    T <node|0> <amber_N> <red_N>      set thresholds
    Z <node|0>                        tare
    I <node>                          identify (blink)
*/
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "esp_arduino_version.h"
#include "cg_protocol.h"

const uint8_t BROADCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Small queue so the ESP-NOW callback (Wi-Fi task) never blocks on Serial.
struct Rx { CgTelemetry p; int8_t rssi; uint8_t mac[6]; };
QueueHandle_t rxQueue;

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
  const uint8_t *mac = info->src_addr;
  int8_t rssi = info->rx_ctrl ? info->rx_ctrl->rssi : 0;
#else
void onRecv(const uint8_t *mac, const uint8_t *data, int len) {
  int8_t rssi = 0;
#endif
  if (len != sizeof(CgTelemetry)) return;
  Rx r; memcpy(&r.p, data, sizeof(CgTelemetry));
  if (r.p.magic != CG_MAGIC || r.p.type != CG_TELEMETRY) return;
  r.rssi = rssi; memcpy(r.mac, mac, 6);
  xQueueSend(rxQueue, &r, 0);
}

void sendCommand(uint8_t target, uint8_t cmd, float a, float b) {
  CgCommand c = {};
  c.magic = CG_MAGIC; c.version = CG_VERSION; c.type = CG_COMMAND;
  c.target = target; c.cmd = cmd; c.a = a; c.b = b;
  esp_now_send(BROADCAST, (uint8_t *)&c, sizeof(c));
}

void handleSerial() {
  static char buf[64]; static int n = 0;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch != '\n' && ch != '\r' && n < 63) { buf[n++] = ch; continue; }
    buf[n] = 0; n = 0;
    int id; float a, b;
    if (sscanf(buf, "L %d %f %f", &id, &a, &b) == 3) sendCommand(id, CMD_SET_LEVEL, a, b);
    else if (sscanf(buf, "T %d %f %f", &id, &a, &b) == 3) sendCommand(id, CMD_SET_THRESH, a, b);
    else if (sscanf(buf, "Z %d", &id) == 1) sendCommand(id, CMD_TARE, 0, 0);
    else if (sscanf(buf, "I %d", &id) == 1) sendCommand(id, CMD_IDENTIFY, 0, 0);
  }
}

void setup() {
  Serial.begin(921600);
  rxQueue = xQueueCreate(64, sizeof(Rx));
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(CG_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) { Serial.println("{\"type\":\"error\",\"msg\":\"espnow init\"}"); return; }
  esp_now_register_recv_cb(onRecv);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST, 6);
  peer.channel = CG_CHANNEL; peer.encrypt = false; peer.ifidx = WIFI_IF_STA;
  esp_now_add_peer(&peer);
  Serial.println("{\"type\":\"gateway\",\"msg\":\"ready\"}");
}

void loop() {
  Rx r;
  while (xQueueReceive(rxQueue, &r, 0) == pdTRUE) {
    const CgTelemetry &p = r.p;
    Serial.printf("{\"type\":\"t\",\"node\":%u,\"seq\":%u,\"ms\":%lu,\"f\":%.1f,\"rate\":%.1f,"
                  "\"peak\":%.1f,\"turb\":%.1f,\"sway\":%.3f,\"lvl\":%u,\"flags\":%u,"
                  "\"bat\":%u,\"rssi\":%d}\n",
                  p.node_id, p.seq, (unsigned long)p.node_ms, p.force_n, p.rate_nps,
                  p.peak_n, p.turbulence_n, p.sway_ms2, p.level, p.flags,
                  p.battery_mv, r.rssi);
  }
  handleSerial();
  delay(1);
}
