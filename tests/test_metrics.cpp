// Host unit test for firmware/node/crush_metrics.h  (g++ -std=c++17)
#include "../firmware/node/crush_metrics.h"
#include <cstdio>
#include <cmath>
#include <cstdlib>
static int fails = 0;
#define CHECK(c, msg) do { if (!(c)) { printf("FAIL: %s\n", msg); fails++; } else printf("ok:   %s\n", msg); } while (0)

int main() {
  const float A = 250, R = 450;
  { // steady light push -> green, ~no turbulence
    CrushMetrics m; uint8_t lvl = 0;
    for (int i = 0; i < 100; i++) { m.add(i * 100, 120 + (rand() % 5 - 2)); lvl = localLevel(lvl, m.force(), m.rate(), m.turbulence(), A, R); }
    CHECK(lvl == 0, "steady 120 N is GREEN");
    CHECK(m.turbulence() < 5, "steady push has low turbulence");
    CHECK(fabs(m.rate()) < 5, "steady push has ~0 rate");
  }
  { // ramp 50 N/s -> rate estimated, amber before force reaches amber threshold
    CrushMetrics m; uint8_t lvl = 0; float fAtAmber = -1;
    for (int i = 0; i < 100; i++) { float f = 5.0f * i; m.add(i * 100, f);
      uint8_t n = localLevel(lvl, m.force(), m.rate(), m.turbulence(), A, R);
      if (n >= 1 && lvl == 0) fAtAmber = m.force(); lvl = n; }
    printf("      rate=%.1f N/s, amber raised at %.0f N\n", m.rate(), fAtAmber);
    CHECK(fabs(m.rate() - 50) < 6, "rate of a 50 N/s ramp is ~50");
    CHECK(fAtAmber > 0 && fAtAmber < A, "rising push triggers AMBER early (prediction)");
    CHECK(lvl == 2, "ramp to 495 N ends RED");
  }
  { // surging: 200 N +/- 120 N at 0.25 Hz -> turbulence raises the level
    CrushMetrics m; uint8_t lvl = 0;
    for (int i = 0; i < 100; i++) { float f = 200 + 120 * sinf(2 * M_PI * 0.25f * i * 0.1f); m.add(i * 100, f);
      lvl = localLevel(lvl, m.force(), m.rate(), m.turbulence(), A, R); }
    printf("      turbulence=%.1f N\n", m.turbulence());
    CHECK(m.turbulence() > 60, "surging crowd shows high turbulence");
    CHECK(lvl >= 1, "surging at 200 N mean is at least AMBER");
  }
  { // hysteresis: hovering around red threshold does not flicker
    CrushMetrics m; uint8_t lvl = 2; int changes = 0;
    for (int i = 0; i < 100; i++) { m.add(i * 100, 440 + (i % 2 ? 8 : -8));
      uint8_t n = localLevel(lvl, m.force(), 0, 0, A, R); if (n != lvl) changes++; lvl = n; }
    CHECK(changes == 0 && lvl == 2, "no flicker near RED threshold");
  }
  { // relief: push released -> back to green
    CrushMetrics m; uint8_t lvl = 2;
    for (int i = 0; i < 80; i++) { m.add(i * 100, 30); lvl = localLevel(lvl, m.force(), m.rate(), m.turbulence(), A, R); }
    CHECK(lvl == 0, "released barricade returns to GREEN");
  }
  printf(fails ? "\n%d FAILED\n" : "\nALL PASSED\n", fails);
  return fails != 0;
}
