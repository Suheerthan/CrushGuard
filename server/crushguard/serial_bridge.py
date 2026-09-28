"""Reads the gateway ESP32 over USB serial and sends commands back to the nodes."""
from __future__ import annotations
import json
import queue
import threading
import time


class SerialBridge:
    def __init__(self, port: str, baud: int = 921600):
        import serial  # pyserial
        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.connected = True
        self.bad_lines = 0
        self._stop = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while not self._stop:
            try:
                chunk = self.ser.read(512)
            except Exception:
                self.connected = False
                time.sleep(1)
                continue
            self.connected = True
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"{"):
                    continue
                try:
                    self.inbox.put(json.loads(line))
                except (ValueError, UnicodeDecodeError):
                    self.bad_lines += 1

    def poll(self) -> list[dict]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def _send(self, text: str):
        try:
            self.ser.write((text + "\n").encode())
        except Exception:
            self.connected = False

    def set_level(self, node: int, level: int, hold_s: float = 3.0):
        self._send(f"L {node} {level} {hold_s}")

    def set_thresholds(self, node: int, amber: float, red: float):
        self._send(f"T {node} {amber} {red}")

    def tare(self, node: int):
        self._send(f"Z {node}")

    def identify(self, node: int):
        self._send(f"I {node}")
