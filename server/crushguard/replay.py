"""Incident replay: rebuild what the control room saw from a session log.

Every session writes
  logs/session-YYYYmmdd-HHMMSS.csv            node telemetry (5 Hz per node)
  logs/session-YYYYmmdd-HHMMSS.events.jsonl   operator / steward actions
The telemetry is fed through a fresh RiskEngine, so the replay shows exactly the
same levels and alerts the live system produced.
"""
from __future__ import annotations
import csv
import json
from pathlib import Path

from .risk import RiskEngine

LEVEL_IDX = {"green": 0, "amber": 1, "red": 2}


def list_sessions(log_dir: Path) -> list[dict]:
    out = []
    if not log_dir.exists():
        return out
    for p in sorted(log_dir.glob("session-*.csv"), reverse=True):
        first = last = None
        rows = 0
        with p.open(newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if not row:
                    continue
                t = float(row[0])
                first = t if first is None else first
                last = t
                rows += 1
        if not rows:
            continue
        out.append({"name": p.stem, "start": first, "end": last,
                    "duration_s": round(last - first, 1), "rows": rows,
                    "size_kb": round(p.stat().st_size / 1024, 1)})
    return out


def build_timeline(log_dir: Path, name: str, config: dict, frame_s: float = 1.0) -> dict:
    path = log_dir / f"{name}.csv"
    if not path.exists() or "/" in name or "\\" in name:
        raise FileNotFoundError(name)
    eng = RiskEngine(config)
    frames = []
    next_eval = next_frame = None
    start = end = None

    def capture(t):
        snap = eng.snapshot(t)
        frames.append({
            "t": round(t, 2), "o": LEVEL_IDX[snap["overall"]], "w": snap["wave"],
            "s": [[s["f"], s["rate"], s["turb"], s["score"],
                   s["eta_s"], LEVEL_IDX[s["level"]], 1 if s["online"] else 0]
                  for s in snap["segments"]],
        })

    with path.open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            t = float(row["time"])
            if start is None:
                start, next_eval, next_frame = t, t, t
            while next_eval is not None and t >= next_eval:
                eng.evaluate(next_eval)
                if next_eval >= next_frame:
                    capture(next_eval)
                    next_frame += frame_s
                next_eval += 0.2
            eng.ingest({"type": "t", "node": int(row["node"]), "f": row["force_n"],
                        "rate": row["rate_nps"], "peak": row["peak_n"], "turb": row["turb_n"],
                        "sway": row["sway"] or 0, "lvl": row["node_level"] or 0,
                        "flags": row["flags"] or 3, "bat": row["bat_mv"] or 0,
                        "rssi": row["rssi"] or 0}, t)
            end = t
    if start is None:
        raise ValueError("empty session")
    eng.evaluate(end)
    capture(end)

    events = []
    ev_path = log_dir / f"{name}.events.jsonl"
    if ev_path.exists():
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    # operator acks / steward responses happened live: attach them to the rebuilt alerts
    alerts = []
    for a in eng.alerts:
        d = a.to_dict()
        d["ended"] = None if a.active else a.updated
        alerts.append(d)
    for e in events:
        if e.get("kind") == "respond":
            for d in alerts:
                if d["segment"] == e.get("segment") and d["started"] <= e["t"] and \
                        (d["ended"] is None or e["t"] <= d["ended"] + 1):
                    d["responder"], d["responded_at"] = e.get("steward"), e["t"]
        if e.get("kind") == "help":
            # help requests are not derivable from telemetry: add them back
            alerts.append({"id": 100000 + len(alerts), "key": f"help:{e.get('segment')}",
                           "severity": "critical", "peak_severity": "critical",
                           "segment": e.get("segment"),
                           "title": f"Steward {e.get('steward')} requests backup at {e.get('segment')}",
                           "action": f"Send more stewards to {e.get('segment')}",
                           "started": e["t"], "updated": e["t"] + 60, "ended": e["t"] + 60,
                           "active": False, "acked": False, "responder": None,
                           "responded_at": None})
    alerts.sort(key=lambda d: d["started"])
    return {
        "name": name, "start": start, "end": end, "duration_s": round(end - start, 1),
        "frame_s": frame_s,
        "thresholds": {"amber_n": eng.amber, "red_n": eng.red},
        "segments": [{"id": s.id, "node": s.node, "label": s.label, "geom": s.geom}
                     for s in eng.segments],
        "frames": frames, "alerts": alerts, "events": events,
    }
