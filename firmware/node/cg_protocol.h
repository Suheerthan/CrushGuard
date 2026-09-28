// CrushGuard wire protocol, shared by node and gateway.
// This file must stay IDENTICAL in firmware/node and firmware/gateway
// (Arduino only compiles files inside the sketch folder, so it is copied).
#pragma once
#include <stdint.h>

#define CG_MAGIC    0xC6
#define CG_VERSION  1
#define CG_CHANNEL  1          // Wi-Fi channel used by every node + gateway

enum CgLevel : uint8_t { CG_GREEN = 0, CG_AMBER = 1, CG_RED = 2 };

enum CgType : uint8_t {
  CG_TELEMETRY = 1,   // node -> gateway, ~5 Hz
  CG_COMMAND   = 2,   // gateway -> node(s)
};

enum CgCmd : uint8_t {
  CMD_SET_LEVEL  = 1, // a = level, b = hold seconds (server override)
  CMD_SET_THRESH = 2, // a = amber N, b = red N
  CMD_TARE       = 3, // zero the load cell
  CMD_IDENTIFY   = 4, // blink all LEDs for 5 s (find a node on site)
};

// flags bits in telemetry
#define CGF_LOADCELL_OK 0x01
#define CGF_IMU_OK      0x02
#define CGF_OVERRIDE    0x04   // level is currently forced by the server
#define CGF_COLLAPSE    0x08   // barricade collapse detected (latched 30 s)

typedef struct __attribute__((packed)) {
  uint8_t  magic;         // CG_MAGIC
  uint8_t  version;       // CG_VERSION
  uint8_t  type;          // CG_TELEMETRY
  uint8_t  node_id;       // 1..250
  uint16_t seq;
  uint16_t battery_mv;
  uint32_t node_ms;
  float    force_n;       // smoothed push force on the barricade (newtons)
  float    rate_nps;      // slope of force over last 2 s (N per second)
  float    peak_n;        // max force in last 1 s
  float    turbulence_n;  // oscillation amplitude (std-dev around trend, last 5 s)
  float    sway_ms2;      // horizontal vibration RMS of the barricade (m/s^2)
  uint8_t  level;         // CgLevel the node is showing on its beacon
  uint8_t  flags;         // CGF_*
  uint16_t tilt_ddeg;     // barricade tilt from its mounting position, 0.1 degree units
} CgTelemetry;            // 40 bytes

typedef struct __attribute__((packed)) {
  uint8_t  magic;
  uint8_t  version;
  uint8_t  type;          // CG_COMMAND
  uint8_t  target;        // node id, 0 = all nodes
  uint8_t  cmd;           // CgCmd
  uint8_t  reserved[3];
  float    a;
  float    b;
} CgCommand;              // 16 bytes
