"""End-to-end tests: simulator -> risk engine, with a fake clock (runs in ~seconds)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(ROOT))

from crushguard.risk import RiskEngine          # noqa: E402
from crushguard.sim import CrowdSimulator       # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def run(sim, eng, t0, seconds, dt=0.1):
    t = t0
    while t < t0 + seconds:
        t += dt
        for m in sim.step(t):
            eng.ingest(m, t)
        eng.evaluate(t)
    return t


def fresh(seed=1):
    return CrowdSimulator(CFG, seed=seed), RiskEngine(CFG)


def levels(eng):
    return {s.id: s.level for s in eng.segments}


def test_calm_crowd_stays_green_with_no_alerts():
    sim, eng = fresh()
    run(sim, eng, 1000.0, 60)
    assert eng.overall() == "green"
    assert not [a for a in eng.alerts if a.active and a.severity != "info"]


def test_buildup_warns_before_critical_and_centre_goes_red():
    sim, eng = fresh()
    t = run(sim, eng, 1000.0, 5)
    sim.set_scenario("buildup")
    first_amber = first_red = None
    while t < 1100 and first_red is None:
        t = run(sim, eng, t, 0.5)
        if first_amber is None and eng.overall() == "amber":
            first_amber = t
            eta = [s.eta_s for s in eng.segments if s.eta_s]
            assert eta, "amber state should come with a time-to-critical estimate"
        if eng.overall() == "red":
            first_red = t
    assert first_amber is not None and first_red is not None
    assert first_red - first_amber >= 5, "operators must get several seconds of warning"
    red = [s.id for s in eng.segments if s.level == 2]
    assert set(red) & {"S3", "S4"}, f"centre of the stage should be worst, got {red}"
    assert any(a.severity == "critical" and a.active for a in eng.alerts)


def test_relief_gate_and_stopping_entry_bring_pressure_down():
    sim, eng = fresh()
    sim.set_scenario("buildup")
    t = run(sim, eng, 1000.0, 40)
    peak = max(s.f for s in eng.segments)
    sim.set_entry(False)
    sim.set_gate("G1", True); sim.set_gate("G2", True)
    run(sim, eng, t, 40)
    assert max(s.f for s in eng.segments) < 0.6 * peak


def test_surge_detects_crowd_wave():
    sim, eng = fresh()
    sim.set_scenario("surge")
    run(sim, eng, 1000.0, 40)
    assert len(eng.wave_segments) >= 3
    assert any(a.key == "wave" and a.active for a in eng.alerts)
    # the server must push RED to nodes that do not see it themselves
    assert all(lvl == 2 for sid, lvl in levels(eng).items() if sid in eng.wave_segments)


def test_single_push_is_local_and_recovers():
    sim, eng = fresh()
    t = run(sim, eng, 1000.0, 5)
    sim.push("S2")
    t = run(sim, eng, t, 1.6)
    assert levels(eng)["S2"] >= 1
    assert levels(eng)["S5"] == 0, "a push on S2 must not raise far segments"
    run(sim, eng, t, 12)
    assert levels(eng)["S2"] == 0
    assert not eng.wave_segments


def test_node_going_silent_raises_offline_alert():
    sim, eng = fresh()
    t = run(sim, eng, 1000.0, 3)
    sim.set_offline("S6", True)
    run(sim, eng, t, 5)
    assert any(a.key == "offline:S6" and a.active for a in eng.alerts)
    seg = next(s for s in eng.snapshot(t + 5)["segments"] if s["id"] == "S6")
    assert seg["online"] is False


def test_pa_text_names_segment_and_gate():
    _, eng = fresh()
    txt = eng.pa_text("S1", "en")
    assert "Front barricade 1" in txt and "Left relief gate" in txt
    assert eng.pa_text("S6", "hi")


def test_barricade_collapse_is_critical_and_named():
    sim, eng = fresh()
    t = run(sim, eng, 1000.0, 5)
    sim.collapse("S4")
    t = run(sim, eng, t, 4)
    seg = next(x for x in eng.segments if x.id == "S4")
    assert seg.level == 2 and seg.tilt > 20
    assert any(a.key == "collapse:S4" and a.severity == "critical" and a.active for a in eng.alerts)
    assert "BARRICADE DOWN" in seg.reasons[0]
    others = {x.id: x.level for x in eng.segments if x.id not in ("S3", "S4", "S5")}
    assert all(v == 0 for v in others.values()), "a collapse must not raise far segments"


def test_heat_lowers_thresholds_and_warns():
    from crushguard.heat import heat_index_c, category
    assert 40 <= heat_index_c(32.2, 70) <= 42          # NWS chart: 90 F, 70 % -> ~106 F
    assert abs(heat_index_c(25, 40) - 25) < 1.5         # mild day: feels like the temperature
    assert category(45)[0] == "danger"
    cool_sim, cool = fresh(seed=4)
    hot_sim, hot = fresh(seed=4)
    cool_sim.set_env(24, 40)
    hot_sim.set_env(38, 60)                              # feels like ~50 C
    for s_, e_ in ((cool_sim, cool), (hot_sim, hot)):
        s_.set_scenario("buildup")
        run(s_, e_, 1000.0, 20)
    assert hot.env(1020)["multiplier"] > cool.env(1020)["multiplier"] == 1.0
    assert max(s.score for s in hot.segments) > max(s.score for s in cool.segments)
    assert any(a.key == "heat" and a.active for a in hot.alerts)
    assert not any(a.key == "heat" for a in cool.alerts)
