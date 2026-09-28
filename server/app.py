"""CrushGuard control-room server.

  python app.py --sim                 # no hardware: built-in crowd simulator
  python app.py --serial COM5         # real nodes via the gateway ESP32 (Windows)
  python app.py --serial /dev/ttyUSB0 # Linux / Mac

Then open http://localhost:8000          control room
          http://<laptop-ip>:8000/steward  steward phones (same Wi-Fi)
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from crushguard.national import NationalNetwork
from crushguard.planner import plan as capacity_plan
from crushguard.replay import build_timeline, list_sessions
from crushguard.risk import RiskEngine
from crushguard.sim import CrowdSimulator, SCENARIOS
from crushguard.stewards import StewardRegistry

ROOT = Path(__file__).parent


class SimCmd(BaseModel):
    action: str             # scenario | push | gate | entry | offline
    value: str | None = None
    on: bool = True


class CheckIn(BaseModel):
    name: str
    segments: list[str] = []


class Respond(BaseModel):
    steward_id: str


class Help(BaseModel):
    segment: str


class Action(BaseModel):
    action: str             # entry | gate | note
    value: str | None = None
    on: bool = True


def lan_ip() -> str:
    """The laptop's address on the local Wi-Fi (what steward phones connect to)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))     # no packet is sent; just picks the interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class Runtime:
    def __init__(self, config: dict, serial_port: str | None, log_dir: Path | None,
                 national_cfg: dict | None, port: int = 8000):
        self.config = config
        self.port = port
        self.engine = RiskEngine(config)
        self.sim = None if serial_port else CrowdSimulator(config)
        self.bridge = None
        if serial_port:
            from crushguard.serial_bridge import SerialBridge
            self.bridge = SerialBridge(serial_port)
        self.stewards = StewardRegistry()
        self.ops = {"entry_open": True, "gates_open": []}   # live mode: operator action log state
        self.national = NationalNetwork(national_cfg, config) if national_cfg else None
        self.national_snap = None
        self.clients: set[WebSocket] = set()
        self.log_dir = log_dir
        self.log_writer = None
        self.event_file = None
        self.session = None
        if log_dir:
            log_dir.mkdir(exist_ok=True)
            self.session = time.strftime("session-%Y%m%d-%H%M%S")
            self.log_file = open(log_dir / f"{self.session}.csv", "w", newline="")
            self.log_writer = csv.writer(self.log_file)
            self.log_writer.writerow(["time", "node", "force_n", "rate_nps", "peak_n",
                                      "turb_n", "sway", "node_level", "flags", "bat_mv", "rssi", "tilt"])
            self.event_file = open(log_dir / f"{self.session}.events.jsonl", "a", encoding="utf-8")

    @property
    def mode(self) -> str:
        return "simulation" if self.sim else "live"

    def log_event(self, kind: str, **data) -> None:
        if self.event_file:
            self.event_file.write(json.dumps({"t": round(time.time(), 2), "kind": kind, **data}) + "\n")
            self.event_file.flush()

    def pull(self) -> None:
        now = time.time()
        msgs = self.sim.step(now) if self.sim else self.bridge.poll()
        for m in msgs:
            self.engine.ingest(m, now)
            if self.log_writer and m.get("type") == "t":
                self.log_writer.writerow([f"{now:.2f}", m["node"], m["f"], m["rate"], m["peak"],
                                          m["turb"], m.get("sway"), m.get("lvl"), m.get("flags"),
                                          m.get("bat"), m.get("rssi"), m.get("tilt", "")])
            elif m.get("type") == "env":
                self.log_event("env", temp=m["temp"], rh=m["rh"])
        if self.national:
            self.national.step(now)

    def state(self) -> dict:
        now = time.time()
        s = self.engine.snapshot(now)
        s["mode"] = self.mode
        s["sim"] = self.sim.status() if self.sim else None
        s["ops"] = ({"entry_open": self.sim.entry_open, "gates_open": sorted(self.sim.gates_open)}
                    if self.sim else self.ops)
        s["gateway_ok"] = True if self.sim else self.bridge.connected
        s["stewards"] = self.stewards.snapshot(now)
        for seg in s["segments"]:
            seg["stewards"] = self.stewards.covering(seg["id"], now)
        s["national"] = self.national_snap
        s["session"] = self.session
        return s

    async def broadcast(self, payload: dict) -> None:
        text = json.dumps(payload)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def loop(self) -> None:
        last_eval = last_push = last_flush = last_nat = 0.0
        while True:
            self.pull()
            now = time.time()
            if self.national and now - last_nat >= 1.0:
                last_nat = now
                self.national_snap = self.national.snapshot(self.engine, self.sim is not None, now)
            if now - last_eval >= 0.2:
                last_eval = now
                self.engine.evaluate(now)
                await self.broadcast({"type": "state", **self.state()})
            if self.bridge and now - last_push >= 1.0:
                last_push = now
                for node, level in self.engine.overrides():
                    self.bridge.set_level(node, level, 3.0)
            if self.log_writer and now - last_flush > 3:
                last_flush = now
                self.log_file.flush()
            await asyncio.sleep(0.05)


def build_app(config_path: Path, serial_port: str | None, log_dir: Path | None,
              national_path: Path | None = None, port: int = 8000) -> FastAPI:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    national_cfg = json.loads(national_path.read_text(encoding="utf-8")) \
        if national_path and national_path.exists() else None
    rt = Runtime(config, serial_port, log_dir, national_cfg, port)
    replay_cache: dict[str, tuple[float, dict]] = {}

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(rt.loop())
        yield
        task.cancel()

    app = FastAPI(title="CrushGuard API", version="2.0", lifespan=lifespan)
    app.state.rt = rt
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    @app.get("/steward", include_in_schema=False)
    def steward_page():
        return FileResponse(ROOT / "static" / "steward.html")

    # ---------- open API for police / temple / railway control rooms ----------
    @app.get("/api/venue", summary="Venue layout, segments, gates")
    def venue():
        return {"venue": config["venue"], "thresholds": config["thresholds"], "mode": rt.mode}

    @app.get("/api/state", summary="Live risk (with 30 s forecast) for every segment + alerts")
    def state():
        return rt.state()

    @app.get("/api/history", summary="Last 2 minutes of force per segment")
    def history():
        return rt.engine.history()

    @app.get("/api/alerts", summary="Alert log")
    def alerts(active_only: bool = False):
        return [a.to_dict() for a in rt.engine.alerts if a.active or not active_only]

    @app.post("/api/alerts/{alert_id}/ack", summary="Acknowledge an alert (control room)")
    def ack(alert_id: int):
        if not rt.engine.ack(alert_id):
            raise HTTPException(404, "no such alert")
        rt.log_event("ack", alert=alert_id)
        return {"ok": True}

    @app.post("/api/alerts/{alert_id}/respond", summary="A steward is responding to an alert")
    def respond(alert_id: int, body: Respond):
        st = rt.stewards.get(body.steward_id)
        if not st:
            raise HTTPException(404, "unknown steward, check in again")
        a = rt.engine.respond(alert_id, st.name)
        if not a:
            raise HTTPException(404, "no such alert")
        st.responding_to, st.responding_since = alert_id, time.time()
        rt.log_event("respond", alert=alert_id, segment=a.segment, steward=st.name)
        return {"ok": True}

    @app.get("/api/pa", summary="Public-address announcement text for a segment")
    def pa(segment: str | None = None, lang: str = "en"):
        rt.log_event("pa", segment=segment, lang=lang)
        return {"lang": lang, "text": rt.engine.pa_text(segment, lang)}

    @app.post("/api/nodes/{node}/identify", summary="Blink a node's beacon to find it on site")
    def identify(node: int):
        if rt.bridge:
            rt.bridge.identify(node)
        return {"ok": True, "mode": rt.mode}

    @app.post("/api/nodes/{node}/tare", summary="Zero a node's load cell")
    def tare(node: int):
        if rt.bridge:
            rt.bridge.tare(node)
        return {"ok": True, "mode": rt.mode}

    @app.post("/api/action", summary="Log an operator action (live mode: stop entry, open gate, note)")
    def action(body: Action):
        if body.action == "entry":
            rt.ops["entry_open"] = body.on
            if rt.sim:
                rt.sim.set_entry(body.on)
        elif body.action == "gate":
            g = set(rt.ops["gates_open"])
            (g.add if body.on else g.discard)(body.value)
            rt.ops["gates_open"] = sorted(g)
            if rt.sim:
                rt.sim.set_gate(body.value, body.on)
        elif body.action != "note":
            raise HTTPException(400, "action must be entry, gate or note")
        rt.log_event("action", action=body.action, value=body.value, on=body.on)
        return rt.state()["ops"]

    @app.post("/api/segments/{seg_id}/restore", summary="Barricade put back up: clear the collapse alarm")
    def restore(seg_id: str):
        rt.engine.clear_collapse(seg_id)
        if rt.sim:
            rt.sim.restore(seg_id)
        rt.log_event("restore", segment=seg_id)
        return {"ok": True}

    @app.post("/api/plan", summary="Pre-event capacity plan: density, egress time, entry queue, sensors")
    def plan_api(body: dict):
        return capacity_plan(body)

    @app.get("/report", include_in_schema=False)
    def report_page():
        return FileResponse(ROOT / "static" / "report.html")

    # ---------- stewards (phones on the ground) ----------
    @app.get("/api/connect", summary="Address steward phones should open")
    def connect_info():
        url = f"http://{lan_ip()}:{rt.port}/steward"
        try:
            import qrcode  # noqa: F401
            has_qr = True
        except ImportError:
            has_qr = False
        return {"steward_url": url, "qr": has_qr}

    @app.get("/api/qr.svg", include_in_schema=False)
    def qr_svg(text: str):
        try:
            import qrcode
            import qrcode.image.svg
        except ImportError:
            raise HTTPException(404, "pip install qrcode")
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        return Response(img.to_string(encoding="unicode"), media_type="image/svg+xml")

    @app.get("/api/stewards", summary="Stewards checked in")
    def stewards():
        return rt.stewards.snapshot()

    @app.post("/api/stewards", summary="Steward check-in from a phone")
    def checkin(body: CheckIn):
        valid = {s.id for s in rt.engine.segments}
        segs = [s for s in body.segments if s in valid]
        st = rt.stewards.checkin(body.name, segs)
        rt.log_event("checkin", steward=st.name, segments=segs)
        return st.to_dict(time.time())

    @app.post("/api/stewards/{sid}/ping", include_in_schema=False)
    def ping(sid: str):
        if not rt.stewards.ping(sid):
            raise HTTPException(404, "unknown steward")
        return {"ok": True}

    @app.post("/api/stewards/{sid}/help", summary="Steward requests backup")
    def help_req(sid: str, body: Help):
        st = rt.stewards.get(sid)
        if not st:
            raise HTTPException(404, "unknown steward")
        a = rt.engine.request_help(body.segment, st.name)
        rt.log_event("help", segment=body.segment, steward=st.name)
        return a.to_dict()

    @app.delete("/api/stewards/{sid}", include_in_schema=False)
    def leave(sid: str):
        rt.stewards.leave(sid)
        return {"ok": True}

    # ---------- replay ----------
    @app.get("/api/sessions", summary="Logged sessions available for replay")
    def sessions():
        items = list_sessions(rt.log_dir) if rt.log_dir else []
        for it in items:
            it["current"] = it["name"] == rt.session
        return items

    @app.get("/api/replay/{name}", summary="Rebuilt timeline of a logged session")
    def replay(name: str):
        if not rt.log_dir:
            raise HTTPException(404, "logging is off")
        path = rt.log_dir / f"{name}.csv"
        if not path.exists() or path.parent != rt.log_dir:
            raise HTTPException(404, "no such session")
        if name == rt.session:
            rt.log_file.flush()
        mtime = path.stat().st_mtime
        cached = replay_cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            tl = build_timeline(rt.log_dir, name, config)
        except ValueError:
            raise HTTPException(400, "session is empty")
        replay_cache[name] = (mtime, tl)
        return tl

    # ---------- national ----------
    @app.get("/api/national", summary="All venues reporting to this control room")
    def national():
        if not rt.national:
            raise HTTPException(404, "national view is off")
        return rt.national_snap or rt.national.snapshot(rt.engine, rt.sim is not None, time.time())

    # ---------- simulator controls ----------
    @app.post("/api/sim", summary="Control the crowd simulator (simulation mode only)")
    def sim(cmd: SimCmd):
        if not rt.sim:
            raise HTTPException(400, "not in simulation mode")
        if cmd.action == "scenario":
            if cmd.value not in SCENARIOS:
                raise HTTPException(400, f"scenario must be one of {SCENARIOS}")
            rt.sim.set_scenario(cmd.value)
        elif cmd.action == "push":
            rt.sim.push(cmd.value)
        elif cmd.action == "gate":
            rt.sim.set_gate(cmd.value, cmd.on)
        elif cmd.action == "entry":
            rt.sim.set_entry(cmd.on)
        elif cmd.action == "offline":
            rt.sim.set_offline(cmd.value, cmd.on)
        elif cmd.action == "collapse":
            if cmd.on:
                rt.sim.collapse(cmd.value)
            else:                                   # barricade put back up
                rt.sim.restore(cmd.value)
                rt.engine.clear_collapse(cmd.value)
        elif cmd.action == "env":
            try:
                t, h = (float(x) for x in (cmd.value or "").split(","))
            except ValueError:
                raise HTTPException(400, "value must be 'temperature,humidity'")
            rt.sim.set_env(t, h)
        else:
            raise HTTPException(400, "unknown action")
        rt.log_event("sim", action=cmd.action, value=cmd.value, on=cmd.on)
        return rt.sim.status()

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        rt.clients.add(sock)
        await sock.send_text(json.dumps({"type": "init", "venue": config["venue"],
                                         "history": rt.engine.history(), **rt.state()}))
        try:
            while True:
                await sock.receive_text()   # keep-alive; commands go through REST
        except WebSocketDisconnect:
            rt.clients.discard(sock)

    return app


def main():
    ap = argparse.ArgumentParser(description="CrushGuard control-room server")
    ap.add_argument("--serial", help="gateway serial port, e.g. COM5 or /dev/ttyUSB0")
    ap.add_argument("--sim", action="store_true", help="use the crowd simulator (default without --serial)")
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--national", default=str(ROOT / "national.json"),
                    help="venues for the National view ('' to turn it off)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-log", action="store_true", help="do not write session logs (disables replay)")
    a = ap.parse_args()
    import uvicorn
    app = build_app(Path(a.config), None if a.sim else a.serial,
                    None if a.no_log else ROOT / "logs",
                    Path(a.national) if a.national else None, a.port)
    print(f"CrushGuard: {'simulation' if a.sim or not a.serial else 'live on ' + a.serial}")
    print(f"  Control room  : http://localhost:{a.port}")
    print(f"  Steward phones: http://{lan_ip()}:{a.port}/steward  (same Wi-Fi)")
    print("Press Ctrl+C in this terminal to stop.")

    class Server(uvicorn.Server):
        # On Windows, Ctrl+C can hang while a dashboard's live connection is open.
        # Ask uvicorn to stop, and force-exit if it has not finished within 3 s.
        def handle_exit(self, sig, frame):
            if not self.should_exit:
                print("\nStopping CrushGuard...")
                t = threading.Timer(3.0, lambda: os._exit(0))
                t.daemon = True
                t.start()
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config(app, host=a.host, port=a.port, log_level="warning",
                                   timeout_graceful_shutdown=2))
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    print("CrushGuard stopped.")


if __name__ == "__main__":
    main()
