# Free-WiLi controller

The Free-WiLi receives wheel commands from the laptop over USB serial and drives the motor driver.

**Open question:** which Free-WiLi model the track provides, and how scripts run on it (on-device script vs host-side Python API). Confirm with the Free-WiLi team on day one, then fill in `controller` here.

## What the firmware must do (spec)

Text lines over USB serial, 115200 baud, newline-terminated.

| Receive | Do |
|---|---|
| `W,<left>,<right>` | Set wheel speeds, -100..100. Map to PWM duty + direction pins on the motor driver. Reset the watchdog. |
| `S,<reason>` | Motors off immediately. Show `STOPPED` + reason on the display. |

| Send | When |
|---|---|
| `T,<state>,<battery_v>` | Once per second. `state` is one of `IDLE`, `DRIVING`, `BLOCKED`, `STOPPED`, `FAULT`. |

**Watchdog (required):** if no `W` line arrives for **500 ms**, motors off and state `STOPPED`. This is what makes unplugging the laptop safe.

**Display:** show the forklift ID (`fl-01`), state and battery so it reads on camera.

## Wiring (TB6612 example)

| Free-WiLi | TB6612 | Notes |
|---|---|---|
| PWM pin A | PWMA | Left motor speed |
| GPIO | AIN1 / AIN2 | Left direction |
| PWM pin B | PWMB | Right motor speed |
| GPIO | BIN1 / BIN2 | Right direction |
| GPIO (high) | STBY | Driver enable |
| GND | GND | **Common ground with motor battery** |

Motor battery goes to VM, never through the Free-WiLi.

## Testing

`uv run python -m fleet_edge.step5_serial_monitor` lets you type `W,40,40` by hand and see the `T` lines coming back. Get this working before step 4.
