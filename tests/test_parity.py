"""The simulator's Python metrics must match the firmware's C++ metrics exactly."""
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))
from crushguard.metrics import CrushMetrics, local_level  # noqa: E402

CPP = r'''
#include "crush_metrics.h"
#include <cstdio>
#include <cmath>
int main() {
  CrushMetrics m; uint8_t lvl = 0;
  for (int i = 0; i < 300; i++) {
    float f = 100 + 1.5f * i + 60 * sinf(i * 0.13f);
    m.add(i * 50, f);
    lvl = localLevel(lvl, m.force(), m.rate(), m.turbulence(), 250, 450);
    if (i % 25 == 24) printf("%.3f %.3f %.3f %.3f %d\n", m.force(), m.rate(), m.peak(), m.turbulence(), lvl);
  }
}
'''


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
def test_python_matches_cpp():
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "p.cpp"
        src.write_text(CPP)
        exe = Path(d) / "p"
        subprocess.check_call(["g++", "-std=c++17", "-O2", "-I", str(REPO / "firmware/node"),
                               str(src), "-o", str(exe)])
        cpp_rows = [list(map(float, l.split())) for l in subprocess.check_output([str(exe)]).decode().split("\n") if l]
    m, lvl, py_rows = CrushMetrics(), 0, []
    for i in range(300):
        f = 100 + 1.5 * i + 60 * math.sin(i * 0.13)
        m.add(i * 50, f)
        lvl = local_level(lvl, m.force(), m.rate(), m.turbulence(), 250, 450)
        if i % 25 == 24:
            py_rows.append([m.force(), m.rate(), m.peak(), m.turbulence(), lvl])
    assert len(cpp_rows) == len(py_rows)
    for c, p in zip(cpp_rows, py_rows):
        for a, b in zip(c, p):
            assert abs(a - b) <= 0.02 * max(1.0, abs(a)), (c, p)
