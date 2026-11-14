"""Talk to the Free-WiLi over USB serial using the Ring 1 text protocol.

  laptop -> Free-WiLi   W,<left>,<right>\\n     wheel speeds -100..100, sent at 20 Hz
  laptop -> Free-WiLi   S,<reason>\\n           stop now
  Free-WiLi -> laptop   T,<state>,<battery_v>\\n telemetry at 1 Hz

Two implementations with the same methods:
  SerialLink - the real Free-WiLi
  SimLink    - a pretend forklift that moves on screen (for --sim and tests)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from . import config


def clamp(v: float, lo: float = -100, hi: float = 100) -> int:
    return int(max(lo, min(hi, round(v))))


def format_wheels(left: float, right: float) -> str:
    return f"W,{clamp(left)},{clamp(right)}\n"


def format_stop(reason: str) -> str:
    return f"S,{reason.replace(',', ' ').replace(chr(10), ' ')}\n"


@dataclass
class FreeWiliTelemetry:
    state: str
    battery_v: float


def parse_line(line: str) -> FreeWiliTelemetry | None:
    """Parse a line from the Free-WiLi. Returns None for anything that isn't telemetry."""
    parts = line.strip().split(",")
    if len(parts) == 3 and parts[0] == "T":
        try:
            return FreeWiliTelemetry(state=parts[1], battery_v=float(parts[2]))
        except ValueError:
            return None
    return None


class SerialLink:
    def __init__(self, port: str = config.SERIAL_PORT, baud: int = config.SERIAL_BAUD):
        import serial  # pyserial
        self.ser = serial.Serial(port, baud, timeout=0)
        self._buf = b""
        self.last_telemetry: FreeWiliTelemetry | None = None

    def wheels(self, left: float, right: float) -> None:
        self.ser.write(format_wheels(left, right).encode())

    def stop(self, reason: str) -> None:
        self.ser.write(format_stop(reason).encode())

    def poll(self) -> FreeWiliTelemetry | None:
        """Read whatever has arrived; return the newest telemetry, if any."""
        self._buf += self.ser.read(1024)
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            t = parse_line(raw.decode(errors="replace"))
            if t:
                self.last_telemetry = t
        return self.last_telemetry

    def close(self) -> None:
        self.stop("shutdown")
        self.ser.close()


class SimLink:
    """Pretend differential-drive forklift. Integrates wheel speeds into a pose."""

    def __init__(self, x=0.3, y=0.2, theta=0.0, top_speed_mps=0.25):
        self.x, self.y, self.theta = x, y, theta
        self.top = top_speed_mps
        self.left = self.right = 0.0
        self.state = "IDLE"
        self._t = time.monotonic()
        self.last_telemetry = FreeWiliTelemetry("IDLE", 7.4)

    def _advance(self) -> None:
        now = time.monotonic()
        dt, self._t = now - self._t, now
        def speed(cmd):  # deadband like a real motor
            return 0.0 if abs(cmd) < config.MIN_SPEED * 0.6 else cmd / 100 * self.top
        vl, vr = speed(self.left), speed(self.right)
        v, w = (vl + vr) / 2, (vr - vl) / config.WHEEL_BASE_M
        self.theta = (self.theta + w * dt + math.pi) % (2 * math.pi) - math.pi
        self.x += v * math.cos(self.theta) * dt
        self.y += v * math.sin(self.theta) * dt

    def wheels(self, left: float, right: float) -> None:
        self._advance()
        self.left, self.right = clamp(left), clamp(right)
        self.state = "DRIVING" if (self.left or self.right) else "IDLE"

    def stop(self, reason: str) -> None:
        self._advance()
        self.left = self.right = 0
        self.state = "STOPPED"

    def poll(self) -> FreeWiliTelemetry:
        self._advance()
        self.last_telemetry = FreeWiliTelemetry(self.state, 7.4)
        return self.last_telemetry

    def close(self) -> None:
        self.stop("shutdown")
