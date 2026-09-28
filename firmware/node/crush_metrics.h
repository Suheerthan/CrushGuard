// Pure C++ signal processing for one barricade node.
// No Arduino dependencies, so it is unit-tested on a laptop (tests/test_metrics.cpp).
#pragma once
#include <stdint.h>
#include <math.h>

class CrushMetrics {
 public:
  static const int N = 512;            // ring buffer (covers >5 s at up to 80 Hz)

  // Feed one force sample. t_ms = millis(), f = newtons.
  void add(uint32_t t_ms, float f) {
    if (count_ == 0) smooth_ = f;
    // light smoothing: removes HX711 jitter but keeps surges (tau ~0.15 s)
    smooth_ += 0.35f * (f - smooth_);
    t_[head_] = t_ms;
    f_[head_] = smooth_;
    head_ = (head_ + 1) % N;
    if (count_ < N) count_++;
    last_t_ = t_ms;
  }

  float force() const { return smooth_; }

  // Least-squares slope of force over the last `win_ms` (N/s).
  float rate(uint32_t win_ms = 2000) const {
    double st = 0, sf = 0, stt = 0, stf = 0; int n = 0;
    for (int i = 0; i < count_; i++) {
      int k = idx(i);
      uint32_t age = last_t_ - t_[k];
      if (age > win_ms) break;
      double t = -(double)age / 1000.0;
      st += t; sf += f_[k]; stt += t * t; stf += t * f_[k]; n++;
    }
    if (n < 3) return 0;
    double den = n * stt - st * st;
    if (fabs(den) < 1e-9) return 0;
    return (float)((n * stf - st * sf) / den);
  }

  float peak(uint32_t win_ms = 1000) const {
    float m = -1e9f;
    for (int i = 0; i < count_; i++) {
      int k = idx(i);
      if (last_t_ - t_[k] > win_ms) break;
      if (f_[k] > m) m = f_[k];
    }
    return count_ ? m : 0;
  }

  // Oscillation ("crowd surging") amplitude: std-dev of force around its linear
  // trend over the last `win_ms`. A steady push gives ~0; rhythmic waves give a
  // large value. This is the early sign of crowd turbulence.
  float turbulence(uint32_t win_ms = 5000) const {
    double st = 0, sf = 0, stt = 0, stf = 0; int n = 0;
    for (int i = 0; i < count_; i++) {
      int k = idx(i);
      uint32_t age = last_t_ - t_[k];
      if (age > win_ms) break;
      double t = -(double)age / 1000.0;
      st += t; sf += f_[k]; stt += t * t; stf += t * f_[k]; n++;
    }
    if (n < 5) return 0;
    double den = n * stt - st * st;
    double b = fabs(den) < 1e-9 ? 0 : (n * stf - st * sf) / den;
    double a = (sf - b * st) / n;
    double ss = 0;
    for (int i = 0; i < n; i++) {
      int k = idx(i);
      double t = -(double)(last_t_ - t_[k]) / 1000.0;
      double r = f_[k] - (a + b * t);
      ss += r * r;
    }
    return (float)sqrt(ss / n);
  }

 private:
  int idx(int i) const { return (head_ - 1 - i + 2 * N) % N; }  // i=0 newest
  uint32_t t_[N] = {0};
  float f_[N] = {0};
  int head_ = 0, count_ = 0;
  uint32_t last_t_ = 0;
  float smooth_ = 0;
};

// Local alarm level with hysteresis, so the beacon does not flicker at a threshold.
// Used when the gateway/server is unreachable: every node protects its own segment.
inline uint8_t localLevel(uint8_t prev, float force, float rate, float turb,
                          float amber_n, float red_n) {
  const float hyst = 0.12f;                     // 12 % hysteresis band
  // Predict 3 s ahead: a fast-rising push is flagged before it reaches the limit.
  float projected = force + (rate > 0 ? rate * 3.0f : 0);
  float effective = projected + 1.5f * turb;    // strong surging counts as extra load
  if (prev == 2) {
    if (effective > red_n * (1 - hyst)) return 2;
    return effective > amber_n ? 1 : 0;
  }
  if (effective >= red_n) return 2;
  if (prev == 1) return effective > amber_n * (1 - hyst) ? 1 : 0;
  return effective >= amber_n ? 1 : 0;
}

// Barricade collapse: the crowd was pushing hard, the force suddenly vanished AND the
// barricade tilted. That is the moment people fall forward, so it is always critical.
// Latched for 30 s so a single event is never missed by the control room.
class CollapseDetector {
 public:
  // recent_peak = max force over the last ~2 s, tilt_deg = tilt from the mounting position
  bool update(uint32_t t_ms, float force, float recent_peak, float tilt_deg, float amber_n) {
    bool loaded = recent_peak >= 0.6f * amber_n;
    bool dropped = force < 0.25f * recent_peak;
    bool tilted = tilt_deg >= 15.0f;
    if (loaded && dropped && tilted) until_ = t_ms + 30000;
    return active(t_ms);
  }
  bool active(uint32_t t_ms) const { return until_ != 0 && (int32_t)(until_ - t_ms) > 0; }
  void clear() { until_ = 0; }
 private:
  uint32_t until_ = 0;
};
