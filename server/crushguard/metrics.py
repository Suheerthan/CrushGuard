"""Python port of firmware/node/crush_metrics.h.

Used by the simulator so simulated nodes behave exactly like real ones.
tests/test_parity.py checks it matches the C++ version.
"""
from collections import deque
import math

GREEN, AMBER, RED = 0, 1, 2


class CrushMetrics:
    def __init__(self, maxlen: int = 512):
        self.buf: deque = deque(maxlen=maxlen)  # (t_ms, smoothed force)
        self.smooth = 0.0

    def add(self, t_ms: int, f: float) -> None:
        if not self.buf:
            self.smooth = f
        self.smooth += 0.35 * (f - self.smooth)
        self.buf.append((t_ms, self.smooth))

    def force(self) -> float:
        return self.smooth

    def _window(self, win_ms):
        if not self.buf:
            return []
        last = self.buf[-1][0]
        out = []
        for t, f in reversed(self.buf):
            if last - t > win_ms:
                break
            out.append((-(last - t) / 1000.0, f))
        return out

    @staticmethod
    def _fit(pts):
        n = len(pts)
        st = sum(p[0] for p in pts)
        sf = sum(p[1] for p in pts)
        stt = sum(p[0] * p[0] for p in pts)
        stf = sum(p[0] * p[1] for p in pts)
        den = n * stt - st * st
        b = 0.0 if abs(den) < 1e-9 else (n * stf - st * sf) / den
        a = (sf - b * st) / n
        return a, b

    def rate(self, win_ms: int = 2000) -> float:
        pts = self._window(win_ms)
        if len(pts) < 3:
            return 0.0
        return self._fit(pts)[1]

    def peak(self, win_ms: int = 1000) -> float:
        pts = self._window(win_ms)
        return max(p[1] for p in pts) if pts else 0.0

    def turbulence(self, win_ms: int = 5000) -> float:
        pts = self._window(win_ms)
        if len(pts) < 5:
            return 0.0
        a, b = self._fit(pts)
        ss = sum((f - (a + b * t)) ** 2 for t, f in pts)
        return math.sqrt(ss / len(pts))


def effective_load(force: float, rate: float, turb: float) -> float:
    """Force predicted 3 s ahead, plus a penalty for surging."""
    return force + (rate * 3.0 if rate > 0 else 0.0) + 1.5 * turb


def local_level(prev: int, force: float, rate: float, turb: float,
                amber: float, red: float) -> int:
    hyst = 0.12
    eff = effective_load(force, rate, turb)
    if prev == RED:
        if eff > red * (1 - hyst):
            return RED
        return AMBER if eff > amber else GREEN
    if eff >= red:
        return RED
    if prev == AMBER:
        return AMBER if eff > amber * (1 - hyst) else GREEN
    return AMBER if eff >= amber else GREEN
