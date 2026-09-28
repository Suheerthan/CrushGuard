"""Pre-event capacity planner.

Answers, before the gates open: how dense will it get, how long does it take to empty the
ground, will the entry gates cope, and how many CrushGuard sensors does the barricade need.

Planning figures (editable in the request):
  * Standing density bands, people per m2: up to 2 comfortable (UK Purple Guide planning
    figure), 2-4 busy, 4-5 very dense (5 is the upper limit for standing crowds, G. Keith
    Still), above 5 dangerous, above 6 crowds become unstable and injuries can start.
  * Flow through gates on level ground: 82 people per metre of width per minute
    (66 on steps or slopes).
These are guidance values for a first check, not a substitute for a professional crowd
safety plan or local rules (for example NDMA crowd-management guidelines).
"""
from __future__ import annotations
import math

BANDS = [  # (upper limit p/m2, key, label)
    (2.0, "green", "Comfortable"),
    (4.0, "amber", "Busy: above the planning figure"),
    (5.0, "amber", "Very dense (upper limit)"),
    (6.0, "red", "Dangerous"),
    (math.inf, "red", "Crush risk: unstable crowd"),
]

NODE_COST_INR = 1200
GATEWAY_COST_INR = 450
SEGMENT_M = 8.0


def band(density: float) -> dict:
    for upper, level, label in BANDS:
        if density <= upper:
            return {"level": level, "label": label}
    return {"level": "red", "label": BANDS[-1][2]}


def plan(p: dict) -> dict:
    area = max(1.0, float(p.get("area_m2", 3000)))
    crowd = max(1.0, float(p.get("crowd", 8000)))
    front_share = min(1.0, max(0.0, float(p.get("front_share", 0.3))))
    front_area = min(area, max(1.0, float(p.get("front_area_m2", area * 0.15))))
    exit_w = max(0.1, float(p.get("exit_width_m", 12)))
    entry_w = max(0.1, float(p.get("entry_width_m", 6)))
    arrival_h = max(0.1, float(p.get("arrival_hours", 2)))
    flow = max(1.0, float(p.get("flow_ppm", 82)))            # people / metre / minute
    peaking = max(1.0, float(p.get("peaking_factor", 1.5)))
    target_egress = max(1.0, float(p.get("target_egress_min", 10)))
    barricade_m = max(0.0, float(p.get("barricade_m", 60)))
    stepped = bool(p.get("stepped", False))
    if stepped:
        flow = min(flow, 66.0)

    avg_d = crowd / area
    front_d = crowd * front_share / front_area
    egress_min = crowd / (exit_w * flow)
    arrival_rate = crowd / (arrival_h * 60) * peaking          # people / minute at the peak
    entry_cap = entry_w * flow
    queue_growth = max(0.0, arrival_rate - entry_cap)           # people / minute
    peak_window_min = arrival_h * 60 / 3                        # the busiest third of arrivals
    queue_peak = queue_growth * peak_window_min
    nodes = math.ceil(barricade_m / SEGMENT_M) if barricade_m else 0

    comfortable_cap = int(area * 2)
    upper_cap = int(area * 5)
    safe_front = int(front_area * 4 / max(front_share, 0.01))   # crowd size that keeps the front at 4 p/m2
    exit_needed = crowd / (flow * target_egress)

    recs = []
    fb, ab = band(front_d), band(avg_d)
    if front_d > 4:
        pens = math.ceil(front_d / 4)
        recs.append(f"Split the front-of-stage area into at least {pens} pens with separate entries, "
                    f"or cap the whole crowd near {safe_front:,} people: the front would reach "
                    f"{front_d:.1f} people/m².")
    if avg_d > 2:
        recs.append(f"Average density {avg_d:.1f} people/m² is above the 2 people/m² planning figure. "
                    f"Comfortable capacity for {area:,.0f} m² is about {comfortable_cap:,} people.")
    if egress_min > target_egress:
        extra = exit_needed - exit_w
        recs.append(f"Emptying the ground takes {egress_min:.1f} min (target {target_egress:.0f} min). "
                    f"Add about {extra:.1f} m of exit width (total {exit_needed:.1f} m).")
    if queue_growth > 0:
        need_w = arrival_rate / flow
        recs.append(f"Entry gates are too narrow for the peak arrival ({arrival_rate:,.0f} people/min vs "
                    f"{entry_cap:,.0f} capacity). A queue of ~{queue_peak:,.0f} people builds up outside: "
                    f"widen entry to {need_w:.1f} m or stagger arrivals.")
    if nodes:
        recs.append(f"Fit {nodes} CrushGuard sensors along {barricade_m:.0f} m of barricade "
                    f"(one per {SEGMENT_M:.0f} m) and put stewards at the front-of-stage segments.")
    if not recs:
        recs.append("The plan is within the planning figures. Keep CrushGuard on the front barricade.")

    worst = "red" if "red" in (fb["level"], ab["level"]) else \
        "amber" if "amber" in (fb["level"], ab["level"]) or egress_min > target_egress or queue_growth > 0 \
        else "green"
    return {
        "overall": worst,
        "avg_density": round(avg_d, 2), "avg_band": ab,
        "front_density": round(front_d, 2), "front_band": fb,
        "egress_min": round(egress_min, 1), "target_egress_min": target_egress,
        "flow_ppm": flow,
        "arrival_rate_ppm": round(arrival_rate), "entry_capacity_ppm": round(entry_cap),
        "queue_peak": round(queue_peak),
        "comfortable_capacity": comfortable_cap, "upper_capacity": upper_cap,
        "safe_crowd_for_front": safe_front,
        "exit_width_needed_m": round(exit_needed, 1),
        "sensors": nodes,
        "cost_inr": nodes * NODE_COST_INR + (GATEWAY_COST_INR if nodes else 0),
        "recommendations": recs,
        "assumptions": {
            "density_bands": "≤2 comfortable (UK Purple Guide), 5 = upper limit for standing (G. K. Still), >6 unstable",
            "flow": f"{flow:.0f} people per metre per minute ({'steps/slopes' if stepped else 'level ground'})",
            "peaking_factor": peaking,
        },
    }
