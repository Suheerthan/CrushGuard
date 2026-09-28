"""Stewards on the ground, checked in from their phones (/steward page)."""
from __future__ import annotations
from dataclasses import dataclass, field
import secrets
import time

ONLINE_AFTER_S = 25.0   # phone pings every 10 s


@dataclass
class Steward:
    id: str
    name: str
    segments: list[str]
    joined: float
    last_seen: float
    responding_to: int | None = None     # alert id
    responding_since: float | None = None

    def to_dict(self, now: float) -> dict:
        return {"id": self.id, "name": self.name, "segments": self.segments,
                "joined": self.joined, "online": now - self.last_seen < ONLINE_AFTER_S,
                "age_s": round(now - self.last_seen, 1),
                "responding_to": self.responding_to, "responding_since": self.responding_since}


@dataclass
class StewardRegistry:
    stewards: dict[str, Steward] = field(default_factory=dict)

    def checkin(self, name: str, segments: list[str], now: float | None = None) -> Steward:
        now = now or time.time()
        name = (name or "Steward").strip()[:40]
        # same name re-joining (phone refreshed) keeps the same identity
        for s in self.stewards.values():
            if s.name.lower() == name.lower():
                s.segments, s.last_seen = segments, now
                return s
        sid = secrets.token_hex(4)
        s = Steward(sid, name, segments, now, now)
        self.stewards[sid] = s
        return s

    def get(self, sid: str) -> Steward | None:
        return self.stewards.get(sid)

    def ping(self, sid: str, now: float | None = None) -> bool:
        s = self.stewards.get(sid)
        if s:
            s.last_seen = now or time.time()
        return s is not None

    def leave(self, sid: str) -> None:
        self.stewards.pop(sid, None)

    def snapshot(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        return sorted((s.to_dict(now) for s in self.stewards.values()),
                      key=lambda d: (not d["online"], d["name"].lower()))

    def covering(self, seg_id: str, now: float | None = None) -> list[str]:
        now = now or time.time()
        return [s.name for s in self.stewards.values()
                if seg_id in s.segments and now - s.last_seen < ONLINE_AFTER_S]
