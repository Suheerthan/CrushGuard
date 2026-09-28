/*
  CrushGuard barricade node (ESP32)
  ---------------------------------
  Measures how hard a crowd is pushing on a barricade and how much it is surging,
  shows a local green/amber/red beacon, and streams telemetry over ESP-NOW.

  Hardware (no extra libraries needed; HX711 and MPU6050 are driven directly):
    HX711  DOUT -> GPIO16   SCK -> GPIO4    VCC 3.3V  GND
    MPU6050 SDA -> GPIO21   SCL -> GPIO22   VCC 3.3V  GND
    LEDs   GREEN -> GPIO25  AMBER -> GPIO26  RED -> GPIO27   (each via 220R to GND)
    Buzzer (active, 3.3-5V) -> GPIO14 (via NPN transistor if >20 mA)
    Battery: LiPo+ -> 100k -> GPIO35 -> 100k -> GND
    Load cell: bar load cell (e.g. 20 kg / 50 kg) inside the clamp, E+/E-/A+/A- to HX711

  USB serial commands (115200 baud), saved in flash:
    id <n>        set node id (flash the same firmware on every node, then set id)
    tare          zero the load cell (nothing touching the barricade)
    cal <newtons> put a known load on the clamp (e.g. 10 kg weight = 98.1) then run
    thr <amber> <red>   local thresholds in newtons
    info          print current settings and readings
*/
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <Wire.h>
#include <Preferences.h>
#include "esp_arduino_version.h"
#include "cg_protocol.h"
#include "crush_metrics.h"

// ---------- pins ----------
const int PIN_HX_DOUT = 16, PIN_HX_SCK = 4;
const int PIN_LED_G = 25, PIN_LED_A = 26, PIN_LED_R = 27, PIN_BUZZ = 14;
const int PIN_BATT = 35;
const uint8_t MPU_ADDR = 0x68;

// ---------- settings (persisted) ----------
Preferences prefs;
uint8_t nodeId = 1;
long    tareRaw = 0;
float   scaleNPerCount = 0.0005f;   // replaced by 'cal'
float   amberN = 250, redN = 450;   // demo defaults; set from the barricade's rated load

// ---------- state ----------
CrushMetrics metrics;
bool loadcellOk = false, imuOk = false;
uint8_t level = CG_GREEN, localLvl = CG_GREEN;
uint8_t overrideLevel = 0; uint32_t overrideUntil = 0;
uint32_t identifyUntil = 0;
uint16_t seq = 0;
float swayRms = 0;
volatile bool pendingTare = false;
const uint8_t BROADCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
portMUX_TYPE hxMux = portMUX_INITIALIZER_UNLOCKED;

// ================= HX711 (bit-banged, gain 128, channel A) =================
bool hxReady() { return digitalRead(PIN_HX_DOUT) == LOW; }

long hxRead() {
  long v = 0;
  portENTER_CRITICAL(&hxMux);            // timing-critical: SCK high must stay < 60 us
  for (int i = 0; i < 24; i++) {
    digitalWrite(PIN_HX_SCK, HIGH); delayMicroseconds(1);
    v = (v << 1) | digitalRead(PIN_HX_DOUT);
    digitalWrite(PIN_HX_SCK, LOW);  delayMicroseconds(1);
  }
  digitalWrite(PIN_HX_SCK, HIGH); delayMicroseconds(1);   // 25th pulse -> gain 128
  digitalWrite(PIN_HX_SCK, LOW);
  portEXIT_CRITICAL(&hxMux);
  if (v & 0x800000) v |= 0xFF000000;    // sign-extend 24-bit
  return v;
}

long hxAverage(int n) {
  long long sum = 0; int got = 0; uint32_t t0 = millis();
  while (got < n && millis() - t0 < 3000) {
    if (hxReady()) { sum += hxRead(); got++; }
    delay(1);
  }
  return got ? (long)(sum / got) : 0;
}

// ================= MPU6050 (raw registers) =================
bool mpuInit() {
  Wire.beginTransmission(MPU_ADDR); Wire.write(0x6B); Wire.write(0x00);   // wake
  if (Wire.endTransmission() != 0) return false;
  Wire.beginTransmission(MPU_ADDR); Wire.write(0x1C); Wire.write(0x08);   // +-4 g
  Wire.endTransmission();
  Wire.beginTransmission(MPU_ADDR); Wire.write(0x1A); Wire.write(0x03);   // DLPF ~44 Hz
  Wire.endTransmission();
  return true;
}

bool mpuReadAccel(float &ax, float &ay, float &az) {
  Wire.beginTransmission(MPU_ADDR); Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)MPU_ADDR, 6) != 6) return false;
  int16_t x = (Wire.read() << 8) | Wire.read();
  int16_t y = (Wire.read() << 8) | Wire.read();
  int16_t z = (Wire.read() << 8) | Wire.read();
  const float k = 9.80665f / 8192.0f;
  ax = x * k; ay = y * k; az = z * k;
  return true;
}

// Horizontal sway: remove the slow part (gravity + mounting tilt) with an EMA,
// then RMS of the rest over ~1 s.
void updateSway() {
  static float bx = 0, by = 0, ms = 0; static bool init = false;
  float ax, ay, az;
  if (!mpuReadAccel(ax, ay, az)) { imuOk = false; return; }
  imuOk = true;
  if (!init) { bx = ax; by = ay; init = true; }
  bx += 0.02f * (ax - bx); by += 0.02f * (ay - by);
  float hx = ax - bx, hy = ay - by;
  ms += 0.01f * ((hx * hx + hy * hy) - ms);   // ~1 s at 100 Hz
  swayRms = sqrtf(ms);
}

// ================= ESP-NOW =================
void handleCommand(const CgCommand &c) {
  if (c.magic != CG_MAGIC || c.type != CG_COMMAND) return;
  if (c.target != 0 && c.target != nodeId) return;
  switch (c.cmd) {
    case CMD_SET_LEVEL:
      overrideLevel = (uint8_t)c.a;
      overrideUntil = millis() + (uint32_t)(c.b * 1000);
      break;
    case CMD_SET_THRESH:
      if (c.a > 0 && c.b > c.a) { amberN = c.a; redN = c.b;
        prefs.putFloat("amber", amberN); prefs.putFloat("red", redN); }
      break;
    case CMD_TARE: pendingTare = true; break;
    case CMD_IDENTIFY: identifyUntil = millis() + 5000; break;
  }
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onRecv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
#else
void onRecv(const uint8_t *mac, const uint8_t *data, int len) {
#endif
  if (len == sizeof(CgCommand)) {
    CgCommand c; memcpy(&c, data, sizeof(c)); handleCommand(c);
  }
}

void espnowInit() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(CG_CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW init failed"); return; }
  esp_now_register_recv_cb(onRecv);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST, 6);
  peer.channel = CG_CHANNEL;
  peer.encrypt = false;
  peer.ifidx = WIFI_IF_STA;
  esp_now_add_peer(&peer);
}

void sendTelemetry() {
  CgTelemetry p = {};
  p.magic = CG_MAGIC; p.version = CG_VERSION; p.type = CG_TELEMETRY;
  p.node_id = nodeId; p.seq = seq++;
  p.battery_mv = (uint16_t)(analogReadMilliVolts(PIN_BATT) * 2);
  p.node_ms = millis();
  p.force_n = metrics.force();
  p.rate_nps = metrics.rate();
  p.peak_n = metrics.peak();
  p.turbulence_n = metrics.turbulence();
  p.sway_ms2 = swayRms;
  p.level = level;
  p.flags = (loadcellOk ? CGF_LOADCELL_OK : 0) | (imuOk ? CGF_IMU_OK : 0) |
            (millis() < overrideUntil ? CGF_OVERRIDE : 0);
  esp_now_send(BROADCAST, (uint8_t *)&p, sizeof(p));
}

// ================= beacon =================
void driveBeacon() {
  uint32_t t = millis();
  bool g = false, a = false, r = false, bz = false;
  if (t < identifyUntil) {                       // "where is node 4?" blink all
    bool on = (t / 150) % 2; g = a = r = on;
  } else if (level == CG_GREEN) {
    g = true;
  } else if (level == CG_AMBER) {
    a = (t / 250) % 2;                           // 2 Hz blink
    bz = (t % 2000) < 80;                        // short chirp every 2 s
  } else {
    r = (t / 100) % 2;                           // 5 Hz blink
    bz = (t % 400) < 200;                        // urgent pulsing
  }
  digitalWrite(PIN_LED_G, g); digitalWrite(PIN_LED_A, a);
  digitalWrite(PIN_LED_R, r); digitalWrite(PIN_BUZZ, bz);
}

// ================= serial console =================
void doTare() {
  tareRaw = hxAverage(20);
  prefs.putLong("tare", tareRaw);
  Serial.printf("tare = %ld\n", tareRaw);
}

void handleSerial() {
  static String line;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch != '\n' && ch != '\r') { line += ch; continue; }
    line.trim();
    if (line.length() == 0) continue;
    if (line.startsWith("id ")) {
      nodeId = (uint8_t)line.substring(3).toInt(); prefs.putUChar("id", nodeId);
      Serial.printf("node id = %u\n", nodeId);
    } else if (line == "tare") {
      doTare();
    } else if (line.startsWith("cal ")) {
      float knownN = line.substring(4).toFloat();
      long raw = hxAverage(20);
      if (knownN > 0 && raw != tareRaw) {
        scaleNPerCount = knownN / (float)(raw - tareRaw);
        prefs.putFloat("scale", scaleNPerCount);
        Serial.printf("scale = %.8f N/count\n", scaleNPerCount);
      } else Serial.println("cal failed: tare first, then load the clamp");
    } else if (line.startsWith("thr ")) {
      float a, r;
      if (sscanf(line.c_str() + 4, "%f %f", &a, &r) == 2 && r > a && a > 0) {
        amberN = a; redN = r; prefs.putFloat("amber", a); prefs.putFloat("red", r);
      }
      Serial.printf("amber=%.0f N red=%.0f N\n", amberN, redN);
    } else if (line == "info") {
      Serial.printf("id=%u tare=%ld scale=%.8f amber=%.0f red=%.0f loadcell=%d imu=%d\n",
                    nodeId, tareRaw, scaleNPerCount, amberN, redN, loadcellOk, imuOk);
      Serial.printf("force=%.1f N rate=%.1f N/s turb=%.1f N sway=%.2f m/s2 level=%u\n",
                    metrics.force(), metrics.rate(), metrics.turbulence(), swayRms, level);
    }
    line = "";
  }
}

// ================= main =================
void setup() {
  Serial.begin(115200);
  pinMode(PIN_HX_SCK, OUTPUT); pinMode(PIN_HX_DOUT, INPUT);
  pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_A, OUTPUT);
  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_BUZZ, OUTPUT);
  analogSetPinAttenuation(PIN_BATT, ADC_11db);

  prefs.begin("crushguard", false);
  nodeId = prefs.getUChar("id", 1);
  tareRaw = prefs.getLong("tare", 0);
  scaleNPerCount = prefs.getFloat("scale", scaleNPerCount);
  amberN = prefs.getFloat("amber", amberN);
  redN = prefs.getFloat("red", redN);

  Wire.begin(21, 22, 400000);
  imuOk = mpuInit();

  // Power-on self test: all LEDs + beep
  digitalWrite(PIN_LED_G, 1); digitalWrite(PIN_LED_A, 1); digitalWrite(PIN_LED_R, 1);
  digitalWrite(PIN_BUZZ, 1); delay(200); digitalWrite(PIN_BUZZ, 0); delay(300);

  uint32_t t0 = millis();
  while (!hxReady() && millis() - t0 < 1000) delay(5);
  loadcellOk = hxReady();
  if (loadcellOk && tareRaw == 0) doTare();       // first boot: auto-zero

  espnowInit();
  Serial.printf("CrushGuard node %u ready (loadcell=%d imu=%d)\n", nodeId, loadcellOk, imuOk);
}

void loop() {
  static uint32_t lastImu = 0, lastTx = 0, lastHx = 0;
  uint32_t now = millis();

  if (pendingTare) { pendingTare = false; doTare(); }

  if (hxReady()) {                               // 10 or 80 Hz depending on HX711 RATE pin
    long raw = hxRead();
    metrics.add(now, (raw - tareRaw) * scaleNPerCount);
    lastHx = now; loadcellOk = true;
  } else if (now - lastHx > 1000) {
    loadcellOk = false;                          // wire cut / HX711 dead -> reported to server
  }

  if (now - lastImu >= 10) { lastImu = now; if (imuOk || now % 5000 < 10) updateSway(); }

  localLvl = localLevel(localLvl, metrics.force(), metrics.rate(), metrics.turbulence(),
                        amberN, redN);
  // Final level = worst of (own judgement, server override). If the server goes
  // silent the override expires and the node keeps protecting its own segment.
  level = localLvl;
  if (now < overrideUntil && overrideLevel > level) level = overrideLevel;

  if (now - lastTx >= 200) { lastTx = now; sendTelemetry(); }   // 5 Hz
  driveBeacon();
  handleSerial();
}
