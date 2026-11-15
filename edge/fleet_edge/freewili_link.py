"""Talk to the Free-WiLi over USB serial using the Ring 1 text protocol.

  laptop -> Free-WiLi   W,<left>,<right>\\n     wheel speeds -100..100, sent at 20 Hz
  laptop -> Free-WiLi   F,<speed>,<ms>\\n       fork motor -100..100 (+ = up) for ms (max 5000)
  laptop -> Free-WiLi   S,<reason>\\n           stop everything now
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


FORK_MAX_MS = 5000   # the firmware caps fork runs here (no end-stop switches)


def format_fork(speed: float, ms: int) -> str:
    return f"F,{clamp(speed)},{int(max(0, min(FORK_MAX_MS, ms)))}\n"


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


MAIN_CPU_PRODUCT = "FWOG main forklift"   # USB product string set by freewili/forklift firmware


def find_port(product: str = MAIN_CPU_PRODUCT) -> str:
    """Find the Free-WiLi OG main CPU. The OG shows up as TWO serial ports (main and
    display CPU); only the main CPU drives the motors."""
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        if p.product and product in p.product:
            return p.device
    seen = ", ".join(f"{p.device} ({p.product})" for p in ports) or "none"
    raise RuntimeError(f"no serial port with product '{product}'. Is the forklift "
                       f"firmware flashed and the Free-WiLi on? Ports seen: {seen}")


class SerialLink:
    def __init__(self, port: str = config.SERIAL_PORT, baud: int = config.SERIAL_BAUD):
        import serial  # pyserial
        if port == "auto":
            port = find_port()
        # "socket://forklift-01.local:5000" = over WiFi through the Bottlenose relay
        # (freewili/bottlenose/); anything else is a USB serial device. Same lines either way.
        self.ser = serial.serial_for_url(port, baudrate=baud, timeout=0)
        self._buf = b""
        self.last_telemetry: FreeWiliTelemetry | None = None

    def wheels(self, left: float, right: float) -> None:
        self.ser.write(format_wheels(left, right).encode())

    def fork(self, speed: float, ms: int) -> None:
        """Run the fork motor (+ = up) for ms, then it stops on its own."""
        self.ser.write(format_fork(speed, ms).encode())

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
        # Like the real chassis, rotate about a point PIVOT_FWD_M ahead of the centre.
        d = config.PIVOT_FWD_M
        px, py = self.x + d * math.cos(self.theta), self.y + d * math.sin(self.theta)
        self.theta = (self.theta + w * dt + math.pi) % (2 * math.pi) - math.pi
        self.x, self.y = px - d * math.cos(self.theta), py - d * math.sin(self.theta)
        self.x += v * math.cos(self.theta) * dt
        self.y += v * math.sin(self.theta) * dt

    def wheels(self, left: float, right: float) -> None:
        self._advance()
        self.left, self.right = clamp(left), clamp(right)
        self.state = "DRIVING" if (self.left or self.right) else "IDLE"

    def fork(self, speed: float, ms: int) -> None:
        pass   # the sim has no fork

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
