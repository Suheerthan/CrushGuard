"""Crowd + sensor simulator.

Lets the team build and demo the whole system before the hardware arrives, and
gives the judges a repeatable scenario. Each simulated node runs the SAME
signal-processing code as the real firmware (metrics.py = crush_metrics.h) and
emits the SAME JSON lines as the real gateway, so the server cannot tell the
difference.
"""
from __future__ import annotations
import math
import random

from .metrics import CrushMetrics, local_level

SCENARIOS = ("calm", "buildup", "surge")


class CrowdSimulator:
    SAMPLE_HZ = 20
    TX_HZ = 5

    def __init__(self, config: dict, seed: int | None = None):
        self.cfg = config
        self.rng = random.Random(seed)
        v = config["venue"]
        self.segs = v["segments"]
        self.ids = [s["id"] for s in self.segs]
        self.front = [s["id"] for s in self.segs if abs(s["y1"] - s["y2"]) < 0.1]
        self.gates = {g["id"]: g for g in v.get("gates", [])}
        self.amber = float(config["thresholds"]["amber_n"])
        self.red = float(config["thresholds"]["red_n"])

        self.scenario = "calm"
        self.scenario_t0 = 0.0
        self.entry_open = True
        self.gates_open: set[str] = set()
        self.offline: set[str] = set()
        self.faulty: set[str] = set()
        self.build = {i: 0.0 for i in self.ids}
        self.wave_amp = 0.0
        self.pushes: list[tuple[str, float]] = []   # (segment, start time)
        self.metrics = {i: CrushMetrics() for i in self.ids}
        self.levels = {i: 0 for i in self.ids}
        self.seq = {i: 0 for i in self.ids}
        self.bat = {i: 4150 - 40 * k for k, i in enumerate(self.ids)}
        self.phase_noise = {i: self.rng.random() * 6.28 for i in self.ids}
        self.t = None
        self._next_sample = 0.0
        self._next_tx = 0.0
        self.true_force = {i: 0.0 for i in self.ids}

    # ---------------- controls (called from the dashboard) ----------------
    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(name)
        self.scenario = name
        self.scenario_t0 = self.t or 0.0
        if name == "calm":
            self.build = {i: 0.0 for i in self.ids}
            self.wave_amp = 0.0
            self.gates_open.clear()
            self.entry_open = True

    def push(self, seg_id: str) -> None:
        if seg_id in self.ids:
            self.pushes.append((seg_id, self.t or 0.0))

    def set_gate(self, gate_id: str, is_open: bool) -> None:
        (self.gates_open.add if is_open else self.gates_open.discard)(gate_id)

    def set_entry(self, is_open: bool) -> None:
        self.entry_open = is_open

    def set_offline(self, seg_id: str, off: bool) -> None:
        (self.offline.add if off else self.offline.discard)(seg_id)

    def status(self) -> dict:
        return {"scenario": self.scenario, "entry_open": self.entry_open,
                "gates_open": sorted(self.gates_open), "offline": sorted(self.offline)}

    # ---------------- crowd physics (very simplified) ----------------
    def _relieved(self, seg_id: str) -> bool:
        return any(seg_id in self.gates[g]["segments"] for g in self.gates_open if g in self.gates)

    def _crowd_step(self, t: float, dt: float) -> None:
        centre = len(self.front) / 2 - 0.5
        for k, sid in enumerate(self.ids):
            b = self.build[sid]
            if self.scenario == "buildup" and self.entry_open and sid in self.front:
                # people keep arriving and press toward the middle of the stage
                w = math.exp(-((self.front.index(sid) - centre) ** 2) / 3.0)
                b += 22.0 * w * dt
            # crowd slowly spreads / relaxes
            b -= (4.0 if not self.entry_open else 1.0) * dt
            if self._relieved(sid):
                b -= 30.0 * dt
            self.build[sid] = min(max(b, 0.0), 900.0)

        # neighbours share load (people get pushed sideways along the barricade)
        fb = [self.build[i] for i in self.front]
        for j, sid in enumerate(self.front):
            left = fb[j - 1] if j > 0 else fb[j]
            right = fb[j + 1] if j < len(fb) - 1 else fb[j]
            self.build[sid] += 0.15 * dt * (left + right - 2 * fb[j])

        target = 0.0
        if self.scenario == "surge":
            target = 170.0 if self.entry_open else 60.0
            if self.gates_open:
                target *= 0.4
        self.wave_amp += (target - self.wave_amp) * min(1.0, 0.05 * dt)

    def _force(self, sid: str, t: float) -> float:
        base = 70.0 + 20.0 * math.sin(0.07 * t + self.phase_noise[sid])
        f = base + self.build[sid]
        if sid in self.front and self.wave_amp > 1:
            j = self.front.index(sid)
            mean = 160.0 * (self.wave_amp / 170.0)
            # a pressure wave travelling sideways along the barricade (~0.2 Hz)
            f += mean + self.wave_amp * math.sin(2 * math.pi * 0.2 * t - 0.9 * j)
        for seg, t0 in self.pushes:
            if seg == sid:
                dtp = t - t0
                if 0 <= dtp < 1.0:
                    f += 380.0 * dtp
                elif 1.0 <= dtp < 6.0:
                    f += 380.0 * math.exp(-(dtp - 1.0) / 1.5)
        return max(0.0, f + self.rng.gauss(0, 4.0))

    # ---------------- main step ----------------
    def step(self, now: float) -> list[dict]:
        """Advance the simulation to `now` (seconds). Returns gateway JSON messages."""
        if self.t is None:
            self.t = now
            self.scenario_t0 = now
            self._next_sample = now
            self._next_tx = now
        out: list[dict] = []
        dt_s = 1.0 / self.SAMPLE_HZ
        while self._next_sample <= now:
            ts = self._next_sample
            self._crowd_step(ts, dt_s)
            for sid in self.ids:
                f = self._force(sid, ts)
                self.true_force[sid] = f
                if sid in self.faulty:
                    continue
                self.metrics[sid].add(int(ts * 1000), f)
            if ts >= self._next_tx:
                self._next_tx += 1.0 / self.TX_HZ
                out.extend(self._telemetry(ts))
            self._next_sample += dt_s
        self.pushes = [(s, t0) for s, t0 in self.pushes if now - t0 < 8]
        self.t = now
        return out

    def _telemetry(self, ts: float) -> list[dict]:
        msgs = []
        for k, s in enumerate(self.segs):
            sid = s["id"]
            if sid in self.offline:
                continue
            m = self.metrics[sid]
            f, r, tb = m.force(), m.rate(), m.turbulence()
            self.levels[sid] = local_level(self.levels[sid], f, r, tb, self.amber, self.red)
            self.seq[sid] = (self.seq[sid] + 1) % 65536
            self.bat[sid] = max(3300, self.bat[sid] - (1 if self.rng.random() < 0.02 else 0))
            msgs.append({
                "type": "t", "node": s["node"], "seq": self.seq[sid], "ms": int(ts * 1000),
                "f": round(f, 1), "rate": round(r, 1), "peak": round(m.peak(), 1),
                "turb": round(tb, 1),
                "sway": round(0.02 + tb / 400 + self.rng.random() * 0.01, 3),
                "lvl": self.levels[sid], "flags": 2 if sid in self.faulty else 3,
                "bat": self.bat[sid], "rssi": -48 - 4 * k + self.rng.randint(-3, 3),
            })
        return msgs
