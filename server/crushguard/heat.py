"""Heat stress factor.

People faint and fall in hot, humid crowds, and a fall can start a crush. CrushGuard
therefore lowers its pressure thresholds when the heat index is high.

Heat index: US National Weather Service formula (Rothfusz regression with the NWS
adjustments), categories as used by the NWS:
    27-32 C caution | 32-39 C extreme caution | 39-51 C danger | >= 52 C extreme danger
The risk multiplier per category is a CrushGuard design choice (not from a standard):
it makes the same push count as more dangerous when people are heat-stressed.
"""
from __future__ import annotations
import math

CATEGORIES = [  # (min heat index C, name, risk multiplier)
    (52.0, "extreme danger", 1.30),
    (39.0, "danger", 1.20),
    (32.0, "extreme caution", 1.10),
    (27.0, "caution", 1.05),
    (-99.0, "normal", 1.00),
]


def heat_index_c(temp_c: float, rh: float) -> float:
    """Apparent temperature in Celsius (NWS heat index)."""
    t = temp_c * 9 / 5 + 32
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        hi = simple
    else:
        hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
              - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
              + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
        if rh < 13 and 80 <= t <= 112:
            hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return (hi - 32) * 5 / 9


def category(hi_c: float) -> tuple[str, float]:
    for lo, name, mult in CATEGORIES:
        if hi_c >= lo:
            return name, mult
    return "normal", 1.0
