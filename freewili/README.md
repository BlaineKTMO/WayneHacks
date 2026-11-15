# Free-WiLi OG forklift controller

The **Free-WiLi OG** drives the forklift: **4-wheel skid-steer drive plus a fork motor**, through **three L298N** drivers. The edge laptop sends commands over USB, over **radio** through a second OG used as a base station (below), or over WiFi through a [Bottlenose](bottlenose/README.md) on the Qwiic pins. The OG turns them into PWM on its breakout header.

```
laptop ──USB──► OG main CPU ──6 header pins──► L298N 1 (front axle) ─► FL, FR motors
   W / F / S lines                       ├──► L298N 2 (rear axle)  ─► RL, RR motors
   ◄── T telemetry (1 Hz)                └──► L298N 3              ─► fork motor
```

The OG has two RP2040 chips:

| CPU | Runs | Source |
|---|---|---|
| **Main** | USB commands, PWM, 500 ms motor watchdog, fork timer, telemetry | [`forklift/main/main.c`](forklift/main/main.c) |
| **Display** | Screen (`FL-01` + state), answers main's breakout requests | [`forklift/display/main.c`](forklift/display/main.c) |

`FL-01` is the forklift's ID (`fl-01` everywhere else: MQTT, DynamoDB, dashboard). The screen shows it in capitals because a lowercase l looks like a 1 in that font.

Both CPUs are built from one CMake project and flashed as **one UF2**: the main UF2 carries the display image inside it. The board support package is [wiliOGbsp](https://github.com/freewili/wiliOGbsp), pinned as a submodule in `wiliOGbsp/`.

## 1. Wiring

**Before anything else, jumper header pin 6 (3.3 V out) to header pin 4 (`V PINS IN`).** Without a voltage on pin 4, no breakout pin moves at all, and it looks exactly like broken firmware.

Wire by **header pin number** (the 1–20 printed on the OG). The GPIO column is the chip's internal number, used in the code; it isn't printed on the board.

| OG header pin | Goes to | Job | (GPIO in code) |
|---|---|---|---|
| **9** | IN1 on **driver 1 and driver 2** | Left side | 8 |
| **17** | IN2 on **driver 1 and driver 2** | Left side | 25 |
| **15** | IN3 on **driver 1 and driver 2** | Right side | 14 |
| **13** | IN4 on **driver 1 and driver 2** | Right side | 15 |
| **1** | IN1 on **driver 3** | Fork | 13 |
| **3** | IN2 on **driver 3** | Fork | 27 |
| **19 or 20** | GND on **all three drivers** + motor battery − | Common ground | |
| **10**, **8** | Bottlenose SDA, SCL (Qwiic) | WiFi, optional ([bottlenose/](bottlenose/README.md)) | 16, 17 |

| Driver | OUT1/OUT2 (channel A) | OUT3/OUT4 (channel B) |
|---|---|---|
| **1** (front axle) | Front-left motor | Front-right motor |
| **2** (rear axle) | Rear-left motor | Rear-right motor |
| **3** | Fork motor | unused |

- **Leave the ENA and ENB jumpers ON** on all three drivers. Speed comes from PWM on the IN pins, so EN must stay high. (Earlier versions of this README said to remove them. That's no longer right.)
- **"Goes to driver 1 and driver 2"** means one wire from the header pin, splitting to the same IN pin on both boards. The L298N inputs draw almost no current, so one pin drives both.
- **If a wheel spins the wrong way,** swap that motor's two wires at its driver's OUT terminals. Don't swap header pins.
- **Motor battery** goes to every driver's `12V` terminal, never through the Free-WiLi. Leave each L298N's 5 V regulator jumper on so it powers its own logic. Use a 2S LiPo (7.4 V) or 6×AA: the L298N loses about 2 V, so 4×AA leaves small motors weak.
- 3.3 V logic from the OG is enough for the L298N's inputs (it needs 2.3 V for a high).
- Header pins 4 and 6 are only the I/O supply. Nothing else connects to them.
- **Header pin 11 is not used.** On two different boards it never followed the firmware (stuck at 0 V on one, 3.3 V on the other). Left IN2 uses pin 17 instead. Pin 17 is also the main CPU's LED, so that LED lights while the left side reverses. There is no spare output left.

These six pins (1, 3, 9, 13, 15, 17) are already outputs in the board's default setup, so the firmware never changes a pin's direction. That BSP path has never been tested on hardware, and we avoid it on purpose.

### Power

| What | Powered by |
|---|---|
| **Free-WiLi OG** | Its own internal battery, charged over the **same USB cable** that carries the commands from the laptop |
| **L298N logic** | Each L298N's onboard 5 V regulator (jumper on), from the motor battery |
| **Motors** | Motor battery → every L298N's `12V` terminal |

The **only** electrical link between the OG and the motor side is **ground**: header pin 19 or 20 → every L298N's GND → motor battery −. Never connect the motor battery, an L298N's `12V` or its `5V` pin to any OG pin. Header pins 2 and 6 are OG *outputs*, and pin 6 only feeds pin 4.

The USB cable has to stay plugged in while driving (it carries the commands), so mount the OG on the forklift and route a long, light cable from above so it doesn't drag or lie across a corner tag.

### Power-on order

**Turn the motor battery on only after the screen shows `IDLE`.** The firmware holds every motor braked from boot, but during the first moment after power-up nothing is in control of the pins yet.

## 2. Install the toolchain (once)

1. In VS Code, install the **Raspberry Pi Pico** extension.
2. Use it to install **Pico SDK 2.3.0** (Import Project on `freewili/forklift` does this). It downloads the SDK, the ARM toolchain (`15_2_Rel1`) and Ninja into `~/.pico-sdk/`. The BSP expects exactly that layout.
3. From the repo root, fetch the BSP. Its `fw` tool only needs pyserial, which `uv sync --group edge` already installed:

```bash
git submodule update --init
```

## 3. Build

Use the **Compile** button in the Pico sidebar (VS Code window opened on `freewili/forklift`), or:

```bash
cd freewili/forklift
cmake --preset target-posix          # `target` on Windows
cmake --build build
```

Output: `build/forklift_main.uf2`. It already contains the display firmware.

## 4. Flash

From the repo root:

```bash
uv run python freewili/wiliOGbsp/tools/fw.py flash forklift --uf2 freewili/forklift/build/forklift_main.uf2
```

`fw flash` finds the main CPU over USB, reboots it into BOOTSEL (no button needed) and copies the UF2. The display CPU updates itself from the main UF2 on the next boot.

A board that has never run an FwOGapp first needs the display bootloader, once: `uv run python freewili/wiliOGbsp/tools/fw.py bootloader`. Ours already has it.

Rules from the BSP worth knowing (details in [wiliOGbsp/AGENTS.md](wiliOGbsp/AGENTS.md)):

- **Never copy a UF2 to the display CPU.** It takes the display CPU off USB, and it has no BOOTSEL button to recover.
- Put only one CPU in BOOTSEL at a time.

## 5. Bring-up, in order

| Step | Do | Pass when |
|---|---|---|
| 1 | Power on, no drivers connected | Screen shows `FL-01` and `IDLE`. `FAULT` means main couldn't take over the pins (see below). |
| 2 | `uv run python -m fleet_edge.step5_serial_monitor` | `T,IDLE,0.00` arrives every second |
| 3 | `uv run python -m fleet_edge.step4_motors`, hold `w` at speed 40 | A multimeter on header pin 9 reads about 40% of 3.3 V (≈1.3 V); pin 17 reads 0 V. Hold `s`: the two swap. |
| 4 | Still in step 4, press `r` repeatedly | Header pin 1 reads about 60% of 3.3 V while it runs; pin 3 reads 0 V |
| 5 | Connect drivers + motors, **wheels in the air**, motor battery on after `IDLE` | `w` turns all four wheels forward, `a`/`d` spin left and right sides opposite ways, `r`/`f` move the fork in short bursts |
| 6 | Unplug USB while driving | Everything stops within 0.5 s |

Step 3 matters: **PWM through the OG's level shifters hasn't been tested on hardware by anyone yet.** If header pin 9 reads 0 V or 3.3 V regardless of speed, PWM isn't getting through. Stop and tell the cloud lead.

## Radio: drive without a cable

A **second Free-WiLi OG** plugged into the laptop acts as a radio base station. It relays the same lines to the forklift over the OG's built-in CC1101 radio. You don't need extra hardware, a network or any wiring on the forklift.

```
laptop ──USB──► base OG ~~~ 915 MHz CC1101 ~~~► forklift OG ──► motors
       ◄─ T ──          ◄~~~ telemetry ~~~~~
```

1. **Flash the base.** Plug in **only the spare OG** (`fw flash` takes the first OG it finds), then:
   ```bash
   uv run python freewili/wiliOGbsp/tools/fw.py flash flbase --uf2 freewili/forklift/build/flbase_main.uf2
   ```
   Its screen shows `BASE`, then `NO RADIO` until it hears the forklift.
2. **Flash the forklift** as usual (section 4). Its firmware already includes the radio.
3. **Unplug the forklift's USB** and plug the base into the laptop. The OG runs on its internal battery. Every laptop tool finds the base automatically (`SERIAL_PORT = "auto"` tries the forklift first, then the base), so nothing else changes.

The base screen then shows the forklift's state (`IDLE`, `DRIVING`, …), and the laptop gets the forklift's `T` lines. If the base hears nothing for 3 s, it sends `T,NO_RADIO,0.00`. If its own radio won't start, it sends `T,RADIO_FAULT,0.00`.

| Detail | Value |
|---|---|
| Band | **915 MHz** (US licence-free 902–928 MHz). Both display apps switch the antenna paths from the 400 MHz default at boot, before the radio may transmit. 433 MHz would only allow intermittent transmissions in the US. |
| Radio | **CC1101 on chip select 1.** The OG has two; CS0's antenna path measured about 25 dB weaker, and nothing got across on it. |
| Power | **+10 dBm** (the CC1101's maximum). The OG's antenna path loses roughly 50 dB. At −10 dBm, a board half a metre away heard the other at about −83 dBm, under the forklift's own −76 dBm of noise from the motor drivers. At +10 dBm both ends hear each other at about −44 dBm. |
| Modulation | GFSK at the driver's bring-up data rate. Its bring-up default (OOK) lost about half the packets. Setting 38.4 kbaud / 20 kHz deviation / 101 kHz bandwidth through the driver's setters made nothing decode, so those are left at bring-up values. |
| Packet | 32 bytes: marker, type, forklift ID 1, sequence number, then the same text line as USB. The CC1101's CRC discards corrupted packets. |
| Timing | About 9 ms of airtime per packet. The forklift replies with telemetry 5 ms after a command arrives (the base needs ~1 ms to get back into receive after sending). |
| Measured | Laptop → base → forklift: 59 of 60 commands at 20 Hz, 0 corrupted. Forklift telemetry reaches the laptop every second. |
| Safety | Lost packets just mean missing commands, and the forklift's **500 ms watchdog** brakes. Same if the base, the laptop or the link stops. |
| Security | None: someone with a CC1101 on the same frequency, sync word and packet format could send commands. Fine for a demo. |

**Diagnostics:** send `?` on either board's USB port to get a radio report line (`R,cs=1,peak_rssi=...,tx=...,rx=...,rxbad=...` plus key registers). `?0` / `?1` switch that board to radio CS0 / CS1 until it reboots. Laptop tools ignore `R` lines.

## Serial protocol

Text lines over USB, newline-terminated. The baud rate is ignored, except that **1200 baud reboots the main CPU into BOOTSEL**, so never open the port at 1200.

| Receive | Do |
|---|---|
| `W,<left>,<right>` | Wheel speeds -100..100 (left = both left motors, right = both right motors). `0` brakes that side. |
| `F,<speed>,<ms>` | Fork motor at -100..100 (+ = up) for `ms` milliseconds, then it brakes on its own. `ms` is capped at **5000**, because there are no end-stop switches and a stalled fork motor overheats. `F,0,0` stops the fork. |
| `S,<reason>` | Brake everything immediately. State `STOPPED`. |
| anything else | Ignored. Does **not** feed the watchdog. |

| Send | When |
|---|---|
| `T,<state>,<battery_v>` | Once per second. `state`: `IDLE`, `DRIVING` (wheels or fork moving), `STOPPED`, `FAULT`. `battery_v` is always `0.00`: the OG can't measure the motor battery. |
| `[forklift] ...` | Debug messages. The laptop ignores them. |

**Watchdog:** while anything is moving, no valid `W`, `F` or `S` line for **500 ms** → everything brakes, state `STOPPED`. So during a fork move, keep sending `W` lines (the control loop already does, at 20 Hz). The next command resumes.

**`FAULT`:** the main CPU couldn't confirm the FPGA's configuration window is closed, so it won't drive header pins 1, 13 and 15. Everything stays braked, and it retries every second. Usual cause: the display CPU isn't running the forklift display app. Reflash.

**Fork speed going down** is less exact than going up: header pin 3 can only be on or off (the chip has no PWM output left for it), so the firmware gets the downward speed by inverting pin 1's PWM. It works, but expect up and down to feel slightly different at the same number.

Unit tests for the protocol logic run on the laptop with no hardware: `uv run pytest freewili`.
