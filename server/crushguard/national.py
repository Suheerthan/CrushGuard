"""National overview: many venues reporting to one state / national control room.

The main venue is the real one (simulator or live sensors). The other venues are
simulated with their own crowd simulator + risk engine, and a simple 'local operator'
that responds to red alerts, so the national map stays alive during a demo.
"""
from __future__ import annotations
import random

from .risk import RiskEngine, LEVEL_NAME
from .sim import CrowdSimulator


def venue_summary(meta: dict, engine: RiskEngine, now: float, simulated: bool) -> dict:
    online = [s for s in engine.segments if s.last_seen > 0 and now - s.last_seen < engine.offline_after]
    active = [a for a in engine.alerts if a.active]
    return {
        **{k: meta[k] for k in ("id", "name", "city", "state", "type", "lat", "lon")},
        "label": meta.get("label"),
        "simulated": simulated,
        "overall": engine.overall(),
        "max_score": round(max((s.score for s in online), default=0.0), 1),
        "max_force": round(max((s.f for s in online), default=0.0), 1),
        "segments": [LEVEL_NAME[s.level] if s in online else "off" for s in engine.segments],
        "sensors_online": len(online),
        "sensors_total": len(engine.segments),
        "critical": sum(1 for a in active if a.severity == "critical"),
        "warning": sum(1 for a in active if a.severity == "warning"),
        "wave": bool(engine.wave_segments),
    }


class VirtualVenue:
    def __init__(self, meta: dict, config: dict, seed: int):
        self.meta = meta
        self.sim = CrowdSimulator(config, seed=seed)
        self.engine = RiskEngine(config)
        self.rng = random.Random(seed * 7 + 1)
        self.next_change = None
        self.red_since = None
        self.responded_at = None
        self._last_eval = 0.0

    def _schedule(self, now: float) -> None:
        if self.next_change is None:
            self.next_change = now + self.rng.uniform(5, 40)
            return
        red = self.engine.overall() == "red"
        if red and self.red_since is None:
            self.red_since = now
        if not red:
            self.red_since = None
        # local operator reacts ~8-15 s after red: stop entry, open gates
        if self.red_since and self.responded_at is None and now - self.red_since > self.rng.uniform(8, 15):
            self.sim.set_entry(False)
            for g in self.sim.gates:
                self.sim.set_gate(g, True)
            self.responded_at = now
        # after the response, return to calm
        if self.responded_at and now - self.responded_at > 35:
            self.sim.set_scenario("calm")
            self.responded_at = None
            self.next_change = now + self.rng.uniform(30, 80)
            return
        if now >= self.next_change and self.responded_at is None:
            if self.sim.scenario == "calm":
                self.sim.set_scenario(self.rng.choices(["buildup", "surge", "calm"], [0.45, 0.25, 0.30])[0])
                self.next_change = now + self.rng.uniform(60, 110)
            else:
                self.sim.set_scenario("calm")
                self.next_change = now + self.rng.uniform(30, 80)

    def step(self, now: float) -> None:
        for m in self.sim.step(now):
            self.engine.ingest(m, now)
        if now - self._last_eval >= 0.5:
            self._last_eval = now
            self.engine.evaluate(now)
            self._schedule(now)

    def summary(self, now: float) -> dict:
        return venue_summary(self.meta, self.engine, now, simulated=True)


class NationalNetwork:
    def __init__(self, national_cfg: dict, venue_config: dict):
        self.main_meta = national_cfg["main"]
        self.venues = [VirtualVenue(v, venue_config, seed=i + 11)
                       for i, v in enumerate(national_cfg.get("virtual", []))]

    def step(self, now: float) -> None:
        for v in self.venues:
            v.step(now)

    def snapshot(self, main_engine: RiskEngine, main_simulated: bool, now: float) -> dict:
        main = venue_summary(self.main_meta, main_engine, now, simulated=main_simulated)
        main["is_main"] = True
        venues = [main] + [v.summary(now) for v in self.venues]
        return {
            "venues": venues,
            "totals": {
                "venues": len(venues),
                "sensors_online": sum(v["sensors_online"] for v in venues),
                "sensors_total": sum(v["sensors_total"] for v in venues),
                "red": sum(1 for v in venues if v["overall"] == "red"),
                "amber": sum(1 for v in venues if v["overall"] == "amber"),
            },
        }
