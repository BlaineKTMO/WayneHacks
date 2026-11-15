# Free-WiLi OG forklift controller

The **Free-WiLi OG** drives the forklift's motors through an **L298N**. The edge laptop sends wheel speeds over USB, and the OG turns them into PWM on its breakout header.

```
laptop (step6_ring1.py) ──USB──► OG main CPU ──header pins──► L298N ──► motors
      W,<l>,<r> / S,<reason>  ◄──  T,<state>,<battery_v> (1 Hz)
```

The OG has two RP2040 chips:

| CPU | Runs | Source |
|---|---|---|
| **Main** | USB commands, PWM, 500 ms motor watchdog, telemetry | [`forklift/main/main.c`](forklift/main/main.c) |
| **Display** | Screen (`fl-01` + state), answers main's breakout requests | [`forklift/display/main.c`](forklift/display/main.c) |

Both are built from one CMake project and flashed as **one UF2**: the main UF2 carries the display image inside it. The board support package is [wiliOGbsp](https://github.com/freewili/wiliOGbsp), pinned as a submodule in `wiliOGbsp/`.

## 1. Wiring

**Before anything else, jumper header pin 6 (3.3 V out) to header pin 4 (`V PINS IN`).** Without a voltage on pin 4, no breakout pin moves at all, and it looks exactly like broken firmware.

Wire by **header pin number** (the 1–20 printed on the OG). The GPIO column is the chip's internal number, used in the code; it isn't printed on the board.

| L298N | Wire to OG header pin | Job | (GPIO in code) |
|---|---|---|---|
| ENA | **9** | Left speed (PWM) | 8 |
| IN1 | **3** | Left direction | 27 |
| IN2 | **1** | Left direction | 13 |
| ENB | **11** | Right speed (PWM) | 11 |
| IN3 | **15** | Right direction | 14 |
| IN4 | **13** | Right direction | 15 |
| GND | **19 or 20** | **Common ground with the motor battery** | |

- **Remove the ENA and ENB jumpers** on the L298N, or the motors ignore the speed signal and run flat out.
- Motor battery goes to the L298N's `12V` terminal, never through the Free-WiLi. Leave the L298N's 5 V regulator jumper on so it powers its own logic. Use a 2S LiPo (7.4 V) or 6×AA: the L298N loses about 2 V, so 4×AA leaves small motors weak.
- 3.3 V logic from the OG is enough for the L298N's inputs (it needs 2.3 V for a high).
- Header pins 4 and 6 are only the I/O supply. Nothing else connects to them.

These six pins are already outputs in the board's default setup, so the firmware never changes a pin's direction. That BSP path has never been tested on hardware, and we avoid it on purpose.

## 2. Install the toolchain (once)

1. In VS Code, install the **Raspberry Pi Pico** extension.
2. Use it to install **Pico SDK 2.3.0**. It downloads the SDK, the ARM toolchain (`15_2_Rel1`) and Ninja into `~/.pico-sdk/`. The BSP expects exactly that layout.
3. From the repo root, fetch the BSP. Its `fw` tool only needs pyserial, which `uv sync --group edge` already installed:

```bash
git submodule update --init
```

## 3. Build

```bash
cd freewili/forklift
cmake --preset target-posix          # `target` on Windows
cmake --build build
```

Output: `build/forklift_main.uf2`. It already contains the display firmware.

## 4. Flash

From the repo root:

```bash
# Once per board: the display CPU's serial bootloader
uv run python freewili/wiliOGbsp/tools/fw.py bootloader

# Every time you change the firmware
uv run python freewili/wiliOGbsp/tools/fw.py flash forklift --uf2 freewili/forklift/build/forklift_main.uf2
```

`fw flash` finds the main CPU over USB, reboots it into BOOTSEL (no button needed) and copies the UF2. The display CPU updates itself from the main UF2 on the next boot.

Rules from the BSP worth knowing (details in [wiliOGbsp/AGENTS.md](wiliOGbsp/AGENTS.md)):

- **Never copy a UF2 to the display CPU.** It takes the display CPU off USB, and it has no BOOTSEL button to recover.
- Put only one CPU in BOOTSEL at a time.

## 5. Bring-up, in order

| Step | Do | Pass when |
|---|---|---|
| 1 | Power on, no motors connected | Screen shows `fl-01` and `IDLE`. `FAULT` means main couldn't take over the direction pins (see below). |
| 2 | `uv run python -m fleet_edge.step5_serial_monitor` | `T,IDLE,0.00` arrives every second |
| 3 | Type `W,40,40` | Screen says `DRIVING`. A multimeter on header pin 9 reads about 40% of 3.3 V (≈1.3 V). After 0.5 s with no new line, it drops to `STOPPED`. |
| 4 | Connect the L298N + motors, wheels in the air, `uv run python -m fleet_edge.step4_motors` | w/a/s/d drive the right wheels in the right directions |
| 5 | Unplug USB while driving | Motors stop within 0.5 s |

Step 3 matters: **PWM through the OG's level shifters hasn't been tested on hardware by anyone yet.** If header pin 9 reads 0 V or 3.3 V regardless of speed, PWM isn't getting through. Stop and tell the cloud lead.

## Serial protocol

Text lines over USB, newline-terminated. The baud rate is ignored, except that **1200 baud reboots the main CPU into BOOTSEL**, so never open the port at 1200.

| Receive | Do |
|---|---|
| `W,<left>,<right>` | Wheel speeds -100..100. `0` brakes that wheel. Feeds the watchdog. |
| `S,<reason>` | Both motors off (coast) immediately. State `STOPPED`. |
| anything else | Ignored. Does **not** feed the watchdog. |

| Send | When |
|---|---|
| `T,<state>,<battery_v>` | Once per second. `state`: `IDLE`, `DRIVING`, `STOPPED`, `FAULT`. `battery_v` is always `0.00`: the OG can't measure the motor battery. |
| `[forklift] ...` | Debug messages. The laptop ignores them. |

**Watchdog:** no valid `W` or `S` line for **500 ms** → motors off, state `STOPPED`. The next `W` line resumes driving.

**`FAULT`:** the main CPU couldn't confirm the FPGA's configuration window is closed, so it won't drive the direction pins on header pins 1, 13 and 15. The motors stay off, and it retries every second. Usual cause: the display CPU isn't running the forklift display app. Reflash.

Unit tests for the protocol logic run on the laptop with no hardware: `uv run pytest freewili`.
