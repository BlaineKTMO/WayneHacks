"""Talk to the Free-WiLi over USB serial using the Ring 1 text protocol.

  laptop -> Free-WiLi   W,<left>,<right>\\n     wheel speeds -100..100, sent at 20 Hz
  laptop -> Free-WiLi   F,<speed>,<ms>\\n       fork motor -100..100 (+ = up) for ms (max 5000)
  laptop -> Free-WiLi   S,<reason>\\n           stop everything now
  Free-WiLi -> laptop   T,<state>,<battery_v>\\n telemetry at 1 Hz

Implementations with the same methods (wheels, fork, stop, poll, close):
  SerialLink - one Free-WiLi on its own port (USB, or WiFi via socket://)
  RadioLink  - one forklift through the shared radio base station (RadioHub): lines go
               out as "<id>:W,..." and its telemetry comes back as "<id>:T,..." (forklift
               1 may also answer as plain "T,...")
  SimLink    - a pretend forklift that moves on screen (for --sim and tests)

open_link(fid) picks the right one from config.FORKLIFT_LINKS.
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


def split_addr(line: str) -> tuple[int, str]:
    """"2:T,IDLE,0.00" -> (2, "T,IDLE,0.00"); no prefix -> forklift 1 (base protocol)."""
    if len(line) > 2 and line[0] in "123456789" and line[1] == ":":
        return int(line[0]), line[2:]
    return 1, line


MAIN_CPU_PRODUCT = "FWOG main forklift"   # USB product string set by freewili/forklift firmware
RADIO_BASE_PRODUCT = "FWOG main flbase"    # the radio base station OG (same lines, over 915 MHz)


def find_port(products: tuple[str, ...] = (MAIN_CPU_PRODUCT, RADIO_BASE_PRODUCT)) -> str:
    """Find the OG to talk to: the forklift itself on USB if it's plugged in, otherwise
    the radio base station (it relays the same lines). Each OG shows up as TWO serial
    ports (main and display CPU); only the main CPU's is the right one."""
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for product in products:
        for p in ports:
            if p.product and product in p.product:
                return p.device
    seen = ", ".join(f"{p.device} ({p.product})" for p in ports) or "none"
    raise RuntimeError(f"no forklift or radio base on USB (looked for {', '.join(products)}). "
                       f"Firmware flashed and the Free-WiLi on? Ports seen: {seen}")


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
        self.last_telemetry_at = 0.0     # time.monotonic() of the last T line (0 = never)
        self.port = port

    def wheels(self, left: float, right: float) -> None:
        self.last_wheels = (round(left), round(right))
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
                self.last_telemetry_at = time.monotonic()
        return self.last_telemetry

    def close(self) -> None:
        self.stop("shutdown")
        self.ser.close()


class RadioHub:
    """The radio base station's one serial port, shared by every forklift it reaches."""

    _hubs: dict[str, "RadioHub"] = {}

    @classmethod
    def on(cls, port: str, baud: int = config.SERIAL_BAUD) -> "RadioHub":
        if port not in cls._hubs:
            cls._hubs[port] = cls(port, baud)
        return cls._hubs[port]

    def __init__(self, port: str, baud: int = config.SERIAL_BAUD):
        import serial  # pyserial
        self.port = port
        self.ser = serial.serial_for_url(port, baudrate=baud, timeout=0)
        self._buf = b""
        self.latest: dict[int, FreeWiliTelemetry] = {}
        self.users = 0

    def send(self, radio_id: int, line: str) -> None:
        self.ser.write(f"{radio_id}:{line}".encode())

    def pump(self) -> None:
        self._buf += self.ser.read(4096)
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            rid, text = split_addr(raw.decode(errors="replace").strip())
            t = parse_line(text)
            if t:
                self.latest[rid] = t

    def release(self) -> None:
        self.users -= 1
        if self.users <= 0:
            self.ser.close()
            RadioHub._hubs.pop(self.port, None)


class RadioLink:
    """One forklift through the radio base station."""

    def __init__(self, hub: RadioHub, radio_id: int):
        self.hub, self.radio_id = hub, radio_id
        hub.users += 1
        self.last_telemetry: FreeWiliTelemetry | None = None

    def wheels(self, left: float, right: float) -> None:
        self.last_wheels = (round(left), round(right))
        self.hub.send(self.radio_id, format_wheels(left, right))

    def fork(self, speed: float, ms: int) -> None:
        self.hub.send(self.radio_id, format_fork(speed, ms))

    def stop(self, reason: str) -> None:
        self.hub.send(self.radio_id, format_stop(reason))

    def poll(self) -> FreeWiliTelemetry | None:
        self.hub.pump()
        self.last_telemetry = self.hub.latest.get(self.radio_id, self.last_telemetry)
        return self.last_telemetry

    def close(self) -> None:
        self.stop("shutdown")
        self.hub.release()


def open_link(fid: str):
    """The link for forklift `fid`, from config.FORKLIFT_LINKS[fid]:
      "auto"   its own USB if plugged in (config.FORKLIFT_USB_PRODUCTS), else the radio base
      "radio"  always through the radio base station (radio id config.FORKLIFT_RADIO_IDS)
      other    an explicit port or URL (/dev/ttyACM0, socket://forklift-01.local:5000)"""
    spec = config.FORKLIFT_LINKS.get(fid, "auto")
    if spec not in ("auto", "radio"):
        return SerialLink(spec)
    if spec == "auto":
        try:
            return SerialLink(find_port((config.FORKLIFT_USB_PRODUCTS[fid],)))
        except (RuntimeError, KeyError):
            pass
    return RadioLink(RadioHub.on(find_port((RADIO_BASE_PRODUCT,))), config.FORKLIFT_RADIO_IDS[fid])


class SimLink:
    """Pretend differential-drive forklift. Integrates wheel speeds into a pose."""

    def __init__(self, x=0.3, y=0.2, theta=0.0, top_speed_mps=0.25):
        self.x, self.y, self.theta = x, y, theta
        self.top = top_speed_mps
        self.left = self.right = 0.0
        self.state = "IDLE"
        self._t = time.monotonic()
        self.last_telemetry = FreeWiliTelemetry("IDLE", 7.4)
        self.last_telemetry_at = time.monotonic()
        self.port = "simulated"

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
        self.last_telemetry_at = time.monotonic()
        return self.last_telemetry

    def close(self) -> None:
        self.stop("shutdown")
