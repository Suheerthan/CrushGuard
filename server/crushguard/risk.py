"""Risk engine: turns node telemetry into segment risk, crowd-wide warnings and alerts.

Per segment:
  score  = 100 * effective_load / red_threshold   (100 = critical)
  eta_s  = seconds until force reaches red at the current rate of rise
Across segments:
  crowd wave  = 3+ neighbouring segments surging at the same time. This is the
                'crowd turbulence' pattern that comes before a crush, so it raises
                the whole zone to RED even if no single segment is over the limit.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
import math
import time

from .metrics import GREEN, AMBER, RED, effective_load

LEVEL_NAME = {GREEN: "green", AMBER: "amber", RED: "red"}
FORECAST_TAU_S = 15.0          # a rise cannot continue forever: extrapolation is damped
FORECAST_STEPS = (0, 5, 10, 15, 20, 25, 30)


def forecast(force: float, rate: float, tau: float = FORECAST_TAU_S) -> list[list[float]]:
    """Where the push force is heading over the next 30 s.

    f(t) = f0 + rate * tau * (1 - exp(-t / tau)): follows the current slope at first,
    then levels off. Same formula is used by the dashboard and the replay.
    """
    return [[t, round(max(0.0, force + rate * tau * (1 - math.exp(-t / tau))), 1)]
            for t in FORECAST_STEPS]


HISTORY_S = 120
_RANK = {"": 0, "info": 1, "warning": 2, "critical": 3}


@dataclass
class Segment:
    id: str
    node: int
    label: str
    geom: tuple
    f: float = 0.0
    rate: float = 0.0
    peak: float = 0.0
    turb: float = 0.0
    sway: float = 0.0
    node_level: int = GREEN
    flags: int = 0
    bat: int = 0
    rssi: int = 0
    last_seen: float = 0.0
    score: float = 0.0
    eta_s: float | None = None
    level: int = GREEN
    reasons: list = field(default_factory=list)
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_S * 5))

    @property
    def online(self) -> bool:
        return self.last_seen > 0

    def to_dict(self, now: float, offline_after: float) -> dict:
        online = self.last_seen > 0 and now - self.last_seen < offline_after
        return {
            "id": self.id, "node": self.node, "label": self.label, "geom": self.geom,
            "f": round(self.f, 1), "rate": round(self.rate, 1), "peak": round(self.peak, 1),
            "turb": round(self.turb, 1), "sway": round(self.sway, 3),
            "score": round(self.score, 1),
            "eta_s": None if self.eta_s is None else round(self.eta_s, 1),
            "level": LEVEL_NAME[self.level], "node_level": LEVEL_NAME[self.node_level],
            "online": online, "bat": self.bat, "rssi": self.rssi,
            "loadcell_ok": bool(self.flags & 1), "imu_ok": bool(self.flags & 2),
            "age_s": None if not self.last_seen else round(now - self.last_seen, 1),
            "reasons": self.reasons,
            "forecast": forecast(self.f, self.rate) if online else [],
        }


@dataclass
class Alert:
    id: int
    key: str
    severity: str          # "info" | "warning" | "critical"
    segment: str | None
    title: str
    action: str
    started: float
    updated: float
    active: bool = True
    acked: bool = False
    peak_severity: str = ""
    responder: str | None = None       # steward who pressed "I'm on it"
    responded_at: float | None = None

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("id", "key", "severity", "peak_severity", "segment", "title", "action",
                 "started", "updated", "active", "acked", "responder", "responded_at")}


class RiskEngine:
    def __init__(self, config: dict):
        self.cfg = config
        v = config["venue"]
        self.amber = float(config["thresholds"]["amber_n"])
        self.red = float(config["thresholds"]["red_n"])
        self.offline_after = float(config["timing"]["offline_after_s"])
        self.low_bat = int(config["timing"]["low_battery_mv"])
        self.segments: list[Segment] = [
            Segment(s["id"], s["node"], s["label"], (s["x1"], s["y1"], s["x2"], s["y2"]))
            for s in v["segments"]]
        self.by_node = {s.node: s for s in self.segments}
        self.gates = v.get("gates", [])
        self.alerts: list[Alert] = []
        self._next_alert = 1
        self.wave_segments: list[str] = []
        self._manual: dict[str, float] = {}   # alert key -> expiry (help requests)
        self.started = time.time()

    # ---------------- input ----------------
    def ingest(self, msg: dict, now: float | None = None) -> None:
        """msg = one gateway JSON line (type 't')."""
        if msg.get("type") != "t":
            return
        seg = self.by_node.get(int(msg["node"]))
        if seg is None:
            return
        now = now or time.time()
        seg.f = float(msg["f"]); seg.rate = float(msg["rate"])
        seg.peak = float(msg["peak"]); seg.turb = float(msg["turb"])
        seg.sway = float(msg.get("sway", 0)); seg.node_level = int(msg.get("lvl", 0))
        seg.flags = int(msg.get("flags", 3)); seg.bat = int(msg.get("bat", 0))
        seg.rssi = int(msg.get("rssi", 0)); seg.last_seen = now
        seg.history.append((round(now, 2), round(seg.f, 1)))

    # ---------------- evaluation ----------------
    def neighbours(self, seg: Segment) -> list[Segment]:
        """Segments whose end points touch this one (barricade line topology)."""
        out = []
        a = [(seg.geom[0], seg.geom[1]), (seg.geom[2], seg.geom[3])]
        for o in self.segments:
            if o is seg:
                continue
            b = [(o.geom[0], o.geom[1]), (o.geom[2], o.geom[3])]
            if any(abs(p[0] - q[0]) < 0.5 and abs(p[1] - q[1]) < 0.5 for p in a for q in b):
                out.append(o)
        return out

    def evaluate(self, now: float | None = None) -> None:
        now = now or time.time()
        amber_score = 100 * self.amber / self.red
        for s in self.segments:
            online = s.last_seen > 0 and now - s.last_seen < self.offline_after
            s.reasons = []
            if not online:
                s.score, s.eta_s = 0.0, None
                continue
            eff = effective_load(s.f, s.rate, s.turb)
            s.score = max(0.0, 100 * eff / self.red)
            if s.f >= self.red:
                s.eta_s = 0.0
            elif s.rate > 2:
                s.eta_s = (self.red - s.f) / s.rate
            else:
                s.eta_s = None
            if s.f >= self.amber:
                s.reasons.append(f"push {s.f:.0f} N")
            if s.rate > 10:
                s.reasons.append(f"rising {s.rate:.0f} N/s")
            if s.turb > 0.12 * self.red:
                s.reasons.append("crowd surging")

        # crowd wave: a connected run of >= 3 surging segments
        surging = {s.id for s in self.segments if s.turb > 0.12 * self.red and s.score > 25}
        wave: set[str] = set()
        for s in self.segments:
            if s.id not in surging:
                continue
            group, stack = {s.id}, [s]
            while stack:
                cur = stack.pop()
                for n in self.neighbours(cur):
                    if n.id in surging and n.id not in group:
                        group.add(n.id); stack.append(n)
            if len(group) >= 3:
                wave |= group
        self.wave_segments = sorted(wave)

        for s in self.segments:
            new = GREEN
            if s.score >= 100:
                new = RED
            elif s.score >= amber_score:
                new = AMBER
            # hysteresis: only step down once clearly below
            if s.level == RED and new < RED and s.score > 88:
                new = RED
            if s.level >= AMBER and new == GREEN and s.score > amber_score * 0.88:
                new = AMBER
            if s.id in wave:
                new = max(new, RED)
                s.reasons.append("crowd wave across zone")
            new = max(new, s.node_level)     # never show less than the node itself shows
            s.level = new
        self._update_alerts(now)

    # ---------------- alerts ----------------
    def gate_for(self, seg_id: str) -> dict | None:
        for g in self.gates:
            if seg_id in g.get("segments", []):
                return g
        return None

    def _raise(self, key, severity, seg, title, action, now):
        for a in self.alerts:
            if a.key == key and a.active:
                a.updated = now
                if severity == "critical" and a.severity != "critical":
                    a.acked = False              # escalation needs a fresh acknowledgement
                a.severity, a.title, a.action = severity, title, action
                if _RANK[severity] > _RANK[a.peak_severity]:
                    a.peak_severity = severity
                return
        self.alerts.append(Alert(self._next_alert, key, severity, seg, title, action, now, now,
                                 peak_severity=severity))
        self._next_alert += 1

    def _update_alerts(self, now):
        live: set[str] = set()
        for s in self.segments:
            online = s.last_seen > 0 and now - s.last_seen < self.offline_after
            gate = self.gate_for(s.id)
            gate_txt = f"open {gate['id']} ({gate['label']})" if gate else "open the nearest relief gate"
            if s.last_seen > 0 and not online:
                k = f"offline:{s.id}"; live.add(k)
                self._raise(k, "warning", s.id, f"{s.id} sensor offline",
                            f"Send a steward to {s.label} and check node {s.node}", now)
                continue
            if not online:
                continue
            if s.level == RED:
                k = f"level:{s.id}"; live.add(k)
                why = ", ".join(s.reasons) or "high pressure"
                self._raise(k, "critical", s.id, f"CRUSH RISK at {s.label} ({why})",
                            f"Stop entry at E1, {gate_txt}, play PA announcement", now)
            elif s.level == AMBER:
                k = f"level:{s.id}"; live.add(k)
                eta = f", critical in ~{s.eta_s:.0f} s" if s.eta_s else ""
                self._raise(k, "warning", s.id, f"Pressure building at {s.label}{eta}",
                            f"Slow down entry, move stewards to {s.id}", now)
            if not s.flags & 1:
                k = f"loadcell:{s.id}"; live.add(k)
                self._raise(k, "warning", s.id, f"{s.id} load cell fault",
                            f"Check clamp wiring on node {s.node}", now)
            if s.bat and s.bat < self.low_bat:
                k = f"bat:{s.id}"; live.add(k)
                self._raise(k, "info", s.id, f"{s.id} battery low ({s.bat} mV)",
                            "Swap battery pack", now)
        if self.wave_segments:
            k = "wave"; live.add(k)
            self._raise(k, "critical", None,
                        f"Crowd wave detected across {', '.join(self.wave_segments)}",
                        "Stop all entry, open relief gates on both sides, PA in all languages", now)
        for k, until in list(self._manual.items()):
            if now < until:
                live.add(k)
            else:
                del self._manual[k]
        for a in self.alerts:
            if a.active and a.key not in live:
                a.active = False
                a.updated = now
        # keep the log bounded
        if len(self.alerts) > 200:
            self.alerts = [a for a in self.alerts if a.active] + \
                          [a for a in self.alerts if not a.active][-150:]

    def ack(self, alert_id: int) -> bool:
        for a in self.alerts:
            if a.id == alert_id:
                a.acked = True
                return True
        return False

    def respond(self, alert_id: int, name: str, now: float | None = None) -> Alert | None:
        for a in self.alerts:
            if a.id == alert_id:
                a.responder, a.responded_at = name, now or time.time()
                return a
        return None

    def request_help(self, seg_id: str, name: str, now: float | None = None) -> Alert:
        """A steward on the ground asks for backup: always a critical alert."""
        now = now or time.time()
        seg = next((s for s in self.segments if s.id == seg_id), None)
        label = seg.label if seg else seg_id
        key = f"help:{seg_id}:{name}"
        self._raise(key, "critical", seg_id, f"Steward {name} requests backup at {label}",
                    f"Send more stewards to {seg_id}", now)
        self._manual[key] = now + 60          # stays active for 60 s
        return next(a for a in self.alerts if a.key == key and a.active)

    # ---------------- output ----------------
    def overall(self) -> str:
        lv = max((s.level for s in self.segments), default=GREEN)
        return LEVEL_NAME[lv]

    def overrides(self) -> list[tuple[int, int]]:
        """(node, level) pairs where the server knows more than the node (e.g. a wave)."""
        return [(s.node, s.level) for s in self.segments if s.level > s.node_level]

    def pa_text(self, seg_id: str | None, lang: str) -> str:
        msgs = self.cfg.get("pa_messages", {})
        tpl = msgs.get(lang) or msgs.get("en", "")
        seg = next((s for s in self.segments if s.id == seg_id), None)
        if seg is None:
            worst = max(self.segments, key=lambda s: s.score)
            seg = worst
        gate = self.gate_for(seg.id)
        return tpl.format(label=seg.label, gate=gate["label"] if gate else "The relief gate")

    def snapshot(self, now: float | None = None) -> dict:
        now = now or time.time()
        return {
            "t": now,
            "overall": self.overall(),
            "thresholds": {"amber_n": self.amber, "red_n": self.red},
            "segments": [s.to_dict(now, self.offline_after) for s in self.segments],
            "wave": self.wave_segments,
            "alerts": [a.to_dict() for a in reversed(self.alerts[-60:])],
        }

    def history(self) -> dict:
        return {s.id: list(s.history) for s in self.segments}
