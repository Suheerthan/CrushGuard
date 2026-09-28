"""CrushGuard control-room server.

  python app.py --sim                 # no hardware: built-in crowd simulator
  python app.py --serial COM5         # real nodes via the gateway ESP32 (Windows)
  python app.py --serial /dev/ttyUSB0 # Linux / Mac

Then open http://localhost:8000
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from crushguard.risk import RiskEngine
from crushguard.sim import CrowdSimulator, SCENARIOS

ROOT = Path(__file__).parent


class SimCmd(BaseModel):
    action: str             # scenario | push | gate | entry | offline
    value: str | None = None
    on: bool = True


class Runtime:
    def __init__(self, config: dict, serial_port: str | None, log_dir: Path | None):
        self.config = config
        self.engine = RiskEngine(config)
        self.sim = None if serial_port else CrowdSimulator(config)
        self.bridge = None
        if serial_port:
            from crushguard.serial_bridge import SerialBridge
            self.bridge = SerialBridge(serial_port)
        self.clients: set[WebSocket] = set()
        self.log_writer = None
        if log_dir:
            log_dir.mkdir(exist_ok=True)
            f = open(log_dir / time.strftime("session-%Y%m%d-%H%M%S.csv"), "w", newline="")
            self.log_file = f
            self.log_writer = csv.writer(f)
            self.log_writer.writerow(["time", "node", "force_n", "rate_nps", "peak_n",
                                      "turb_n", "sway", "node_level", "flags", "bat_mv", "rssi"])

    @property
    def mode(self) -> str:
        return "simulation" if self.sim else "live"

    def pull(self) -> None:
        now = time.time()
        msgs = self.sim.step(now) if self.sim else self.bridge.poll()
        for m in msgs:
            self.engine.ingest(m, now)
            if self.log_writer and m.get("type") == "t":
                self.log_writer.writerow([f"{now:.2f}", m["node"], m["f"], m["rate"], m["peak"],
                                          m["turb"], m.get("sway"), m.get("lvl"), m.get("flags"),
                                          m.get("bat"), m.get("rssi")])

    def state(self) -> dict:
        s = self.engine.snapshot()
        s["mode"] = self.mode
        s["sim"] = self.sim.status() if self.sim else None
        s["gateway_ok"] = True if self.sim else self.bridge.connected
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
        last_eval = last_push = last_flush = 0.0
        while True:
            self.pull()
            now = time.time()
            if now - last_eval >= 0.2:
                last_eval = now
                self.engine.evaluate(now)
                await self.broadcast({"type": "state", **self.state()})
            if self.bridge and now - last_push >= 1.0:
                last_push = now
                for node, level in self.engine.overrides():
                    self.bridge.set_level(node, level, 3.0)
            if self.log_writer and now - last_flush > 5:
                last_flush = now
                self.log_file.flush()
            await asyncio.sleep(0.05)


def build_app(config_path: Path, serial_port: str | None, log_dir: Path | None) -> FastAPI:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rt = Runtime(config, serial_port, log_dir)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(rt.loop())
        yield
        task.cancel()

    app = FastAPI(title="CrushGuard API", version="1.0", lifespan=lifespan)
    app.state.rt = rt
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    # ---------- open API for police / temple / railway control rooms ----------
    @app.get("/api/venue", summary="Venue layout, segments, gates")
    def venue():
        return {"venue": config["venue"], "thresholds": config["thresholds"], "mode": rt.mode}

    @app.get("/api/state", summary="Live risk for every barricade segment + alerts")
    def state():
        return rt.state()

    @app.get("/api/history", summary="Last 2 minutes of force per segment")
    def history():
        return rt.engine.history()

    @app.get("/api/alerts", summary="Alert log")
    def alerts(active_only: bool = False):
        return [a.to_dict() for a in rt.engine.alerts if a.active or not active_only]

    @app.post("/api/alerts/{alert_id}/ack", summary="Acknowledge an alert")
    def ack(alert_id: int):
        if not rt.engine.ack(alert_id):
            raise HTTPException(404, "no such alert")
        return {"ok": True}

    @app.get("/api/pa", summary="Public-address announcement text for a segment")
    def pa(segment: str | None = None, lang: str = "en"):
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
        else:
            raise HTTPException(400, "unknown action")
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
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-log", action="store_true", help="do not write CSV session logs")
    a = ap.parse_args()
    import uvicorn
    app = build_app(Path(a.config), None if a.sim else a.serial,
                    None if a.no_log else ROOT / "logs")
    print(f"CrushGuard: {'simulation' if a.sim or not a.serial else 'live on ' + a.serial} "
          f"-> http://localhost:{a.port}")
    print("Press Ctrl+C in this terminal to stop.")

    class Server(uvicorn.Server):
        # On Windows, Ctrl+C can hang while the dashboard's live connection is open.
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
