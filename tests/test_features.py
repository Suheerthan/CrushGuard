"""Forecast, stewards, replay and national view (API level, real server loop)."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient      # noqa: E402

from app import build_app                        # noqa: E402
from crushguard.replay import build_timeline     # noqa: E402
from crushguard.risk import forecast             # noqa: E402

CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_forecast_follows_slope_then_levels_off():
    fc = dict((t, f) for t, f in forecast(200, 20))
    assert fc[0] == 200
    assert 270 < fc[5] < 300           # close to 200 + 20*5 at first
    assert fc[30] < 200 + 20 * 30      # damped, not a straight line to infinity
    assert all(f >= 0 for _, f in forecast(50, -40))


def test_steward_flow_forecast_replay_national(tmp_path):
    app = build_app(ROOT / "config.json", None, tmp_path, ROOT / "national.json", 8000)
    with TestClient(app) as c:
        time.sleep(2.5)
        st = c.get("/api/state").json()
        assert st["segments"][0]["forecast"][0][0] == 0
        # steward checks in from a phone
        s = c.post("/api/stewards", json={"name": "Ravi", "segments": ["S3", "S4", "BAD"]}).json()
        assert s["segments"] == ["S3", "S4"]
        assert c.post(f"/api/stewards/{s['id']}/ping").json()["ok"]
        st = c.get("/api/state").json()
        assert next(x for x in st["segments"] if x["id"] == "S3")["stewards"] == ["Ravi"]
        # crowd builds up -> alert -> steward responds
        c.post("/api/sim", json={"action": "scenario", "value": "buildup"})
        alert = None
        for _ in range(60):
            time.sleep(0.5)
            active = [a for a in c.get("/api/alerts?active_only=true").json()
                      if a["segment"] in ("S3", "S4")]
            if active:
                alert = active[0]
                break
        assert alert, "build-up should raise an alert at the centre"
        assert c.post(f"/api/alerts/{alert['id']}/respond", json={"steward_id": s["id"]}).json()["ok"]
        a2 = next(a for a in c.get("/api/alerts").json() if a["id"] == alert["id"])
        assert a2["responder"] == "Ravi"
        # help request
        h = c.post(f"/api/stewards/{s['id']}/help", json={"segment": "S4"}).json()
        assert h["severity"] == "critical" and "Ravi" in h["title"]
        # national view
        nat = c.get("/api/national").json()
        assert nat["venues"][0]["is_main"] and len(nat["venues"]) == 8
        assert all(v["simulated"] for v in nat["venues"])
        # qr for steward phones
        info = c.get("/api/connect").json()
        assert info["steward_url"].endswith("/steward")
        assert c.get("/api/qr.svg", params={"text": info["steward_url"]}).text.lstrip().startswith("<")
        time.sleep(3.5)                     # let the log flush
        sessions = c.get("/api/sessions").json()
        assert sessions and sessions[0]["current"]
        tl = c.get(f"/api/replay/{sessions[0]['name']}").json()
        assert len(tl["frames"]) >= 5
        assert any(e["kind"] == "respond" and e["steward"] == "Ravi" for e in tl["events"])
        assert any(a["responder"] == "Ravi" for a in tl["alerts"])
        assert c.get("/api/replay/..%2Fapp").status_code == 404


def test_replay_matches_live_engine(tmp_path):
    """Replaying a log reproduces the alerts the live engine raised."""
    from crushguard.risk import RiskEngine
    from crushguard.sim import CrowdSimulator
    sim, eng = CrowdSimulator(CFG, seed=3), RiskEngine(CFG)
    sim.set_scenario("buildup")
    p = tmp_path / "session-test.csv"
    with p.open("w") as f:
        f.write("time,node,force_n,rate_nps,peak_n,turb_n,sway,node_level,flags,bat_mv,rssi\n")
        t = 5000.0
        while t < 5060:
            t += 0.1
            for m in sim.step(t):
                eng.ingest(m, t)
                f.write(f"{t:.2f},{m['node']},{m['f']},{m['rate']},{m['peak']},{m['turb']},"
                        f"{m['sway']},{m['lvl']},{m['flags']},{m['bat']},{m['rssi']}\n")
            eng.evaluate(t)
    tl = build_timeline(tmp_path, "session-test", CFG)
    live_red = {a.segment for a in eng.alerts if a.severity == "critical"}
    replay_red = {a["segment"] for a in tl["alerts"] if a["peak_severity"] == "critical"}
    assert live_red and live_red == replay_red
    assert tl["frames"][-1]["o"] == 2
