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

    events = []
    ev_path = log_dir / f"{name}.events.jsonl"
    if ev_path.exists():
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    # events that change the engine's state are replayed at the moment they happened
    env_events = sorted((e for e in events if e.get("kind") in ("env", "restore")), key=lambda e: e["t"])
    env_i = 0

    with path.open(newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            t = float(row["time"])
            while env_i < len(env_events) and env_events[env_i]["t"] <= t:
                e = env_events[env_i]
                if e["kind"] == "env":
                    eng.ingest({"type": "env", "temp": e["temp"], "rh": e["rh"]}, e["t"])
                else:
                    eng.clear_collapse(e.get("segment"))
                env_i += 1
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
                        "rssi": row["rssi"] or 0, "tilt": row.get("tilt") or 0}, t)
            end = t
    if start is None:
        raise ValueError("empty session")
    eng.evaluate(end)
    capture(end)

    events = [e for e in events if e.get("kind") != "env"]   # keep the log readable
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
    tl = {
        "name": name, "start": start, "end": end, "duration_s": round(end - start, 1),
        "frame_s": frame_s,
        "thresholds": {"amber_n": eng.amber, "red_n": eng.red},
        "segments": [{"id": s.id, "node": s.node, "label": s.label, "geom": s.geom}
                     for s in eng.segments],
        "frames": frames, "alerts": alerts, "events": events,
    }
    tl["stats"] = compute_stats(tl)
    return tl


def _is_mitigation(e: dict) -> str | None:
    """Operator actions that reduce crowd pressure (from the simulator or the live action log)."""
    kind, action = e.get("kind"), e.get("action")
    if kind in ("sim", "action"):
        if action == "entry" and not e.get("on", True):
            return "Entry stopped"
        if action == "gate" and e.get("on", True):
            return f"{e.get('value')} opened"
    if kind == "pa":
        return "PA announcement"
    return None


def compute_stats(tl: dict) -> dict:
    """Response-time and exposure statistics for one session (used by Replay and the report)."""
    frames, fs = tl["frames"], tl["frame_s"]
    segs = tl["segments"]
    per_seg = []
    for k, s in enumerate(segs):
        vals = [fr["s"][k] for fr in frames]
        per_seg.append({
            "id": s["id"], "label": s["label"],
            "peak_force_n": round(max((v[0] for v in vals), default=0), 1),
            "max_score": round(max((v[3] for v in vals), default=0), 1),
            "red_s": round(sum(fs for v in vals if v[5] == 2), 1),
            "amber_s": round(sum(fs for v in vals if v[5] == 1), 1),
        })

    events = sorted(tl["events"], key=lambda e: e["t"])
    incidents = []
    i = 0
    while i < len(frames):
        if frames[i]["o"] != 2:
            i += 1
            continue
        j = i
        while j + 1 < len(frames) and frames[j + 1]["o"] == 2:
            j += 1
        a = i
        while a - 1 >= 0 and frames[a - 1]["o"] >= 1:
            a -= 1
        start, end = frames[i]["t"], frames[j]["t"] + fs
        amber_start = frames[a]["t"]
        involved = sorted({segs[k]["id"] for fr in frames[i:j + 1] for k, v in enumerate(fr["s"]) if v[5] == 2})
        peak = max(v[0] for fr in frames[i:j + 1] for v in fr["s"])
        # replay frames are sampled every `fs` seconds, so an action taken in the same second the
        # warning started can be stamped just before the first amber frame: allow that slack
        win0 = amber_start - max(fs, 2.0)
        resp = next((e for e in events if e.get("kind") == "respond" and win0 <= e["t"] <= end + 30), None)
        act = next(((e, _is_mitigation(e)) for e in events
                    if _is_mitigation(e) and win0 <= e["t"] <= end), (None, None))
        kinds = {al["key"].split(":")[0] for al in tl["alerts"]
                 if al.get("key") and amber_start <= al["started"] <= end}
        incidents.append({
            "start": start, "end": end, "duration_s": round(end - start, 1),
            "warning_lead_s": round(start - amber_start, 1),
            "segments": involved, "peak_force_n": round(peak, 1),
            "collapse": "collapse" in kinds, "wave": "wave" in kinds, "heat": "heat" in kinds,
            "steward": resp.get("steward") if resp else None,
            "steward_delay_s": round(resp["t"] - start, 1) if resp else None,
            "first_action": act[1],
            "action_delay_s": round(act[0]["t"] - start, 1) if act[0] else None,
        })
        i = j + 1

    resp_times = sorted(al["responded_at"] - al["started"] for al in tl["alerts"] if al.get("responded_at"))
    median = (lambda xs: None if not xs else round(xs[len(xs) // 2], 1))
    return {
        "segments": per_seg,
        "incidents": incidents,
        "alerts": {
            "critical": sum(1 for al in tl["alerts"] if al.get("peak_severity") == "critical"),
            "warning": sum(1 for al in tl["alerts"] if al.get("peak_severity") == "warning"),
            "info": sum(1 for al in tl["alerts"] if al.get("peak_severity") == "info"),
            "acked": sum(1 for e in events if e.get("kind") == "ack"),
            "median_steward_response_s": median(resp_times),
        },
        "time_red_s": round(sum(fs for fr in frames if fr["o"] == 2), 1),
        "time_amber_s": round(sum(fs for fr in frames if fr["o"] == 1), 1),
        "peak_force_n": max((p["peak_force_n"] for p in per_seg), default=0),
        "stewards": sorted({e.get("steward") for e in events if e.get("kind") == "checkin"}),
        "actions": [{"t": e["t"], "what": _is_mitigation(e)} for e in events if _is_mitigation(e)],
    }
