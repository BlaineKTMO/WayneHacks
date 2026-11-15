# Bottlenose WiFi relay

Drive the forklift over WiFi instead of a USB cable. The **Bottlenose WiFi Orca** (ESP32-C6-MINI-1) rides on the forklift, joins your WiFi network, and relays the laptop's commands to the OG over I2C (its Qwiic port).

```
laptop ──WiFi, TCP port 5000──► Bottlenose ──Qwiic / I2C──► OG header 8/10 ──► motors
       ◄────── T telemetry ─────           ◄──                ◄──
```

It's the same text protocol as USB (`W`, `F`, `S` in, `T` out; see [../README.md](../README.md#serial-protocol)), the same 500 ms motor watchdog, and every laptop tool works unchanged. USB keeps working too.

## 1. Wiring (4 wires, Qwiic cable)

| Qwiic wire | OG header pin |
|---|---|
| SDA (blue) | **10** |
| SCL (yellow) | **8** |
| 3.3 V (red) | **6** (shares the pin 6 → 4 jumper; that's fine) |
| GND (black) | **19 or 20** |

Pins 8 and 10 are the OG's breakout I2C bus. They're free: the motors use 1, 3, 9, 13, 15 and 17. Don't plug the Bottlenose onto the header's Orca UART: its TX pin (9) drives the left wheels.

If the Bottlenose draws too much for pin 6 (the OG's I/O 3.3 V rail), power it from its own USB-C instead and connect only SDA, SCL and GND.

## 2. Firmware: forklift OG

The OG's forklift firmware listens for the Bottlenose on I2C (address `0x42`). Build and flash as in [../README.md](../README.md) sections 3–4. Nothing else to set.

## 3. Firmware: Bottlenose

This replaces the Bottlenose's stock firmware. To go back, reflash the stock image with FREE-WILi's ESP32 flasher.

1. **WiFi details.** Copy the template and fill in your network. `secrets.h` is git-ignored, so the password never gets committed.
   ```bash
   cd freewili/bottlenose/forklift_wifi
   cp secrets.h.example secrets.h      # then edit WIFI_SSID / WIFI_PASS
   ```
2. **Toolchain (once).** Install [arduino-cli](https://arduino.github.io/arduino-cli/latest/installation/), then the ESP32 core (version 3.x or later supports the C6):
   ```bash
   arduino-cli config init
   arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
   arduino-cli core update-index
   arduino-cli core install esp32:esp32
   ```
3. **Build and flash.** Plug the Bottlenose into the laptop by USB-C:
   ```bash
   arduino-cli compile -b esp32:esp32:esp32c6:CDCOnBoot=cdc freewili/bottlenose/forklift_wifi
   arduino-cli upload  -b esp32:esp32:esp32c6:CDCOnBoot=cdc -p /dev/ttyACM0 freewili/bottlenose/forklift_wifi
   ```
   Use the Bottlenose's port, not the OG's. Run `arduino-cli board list` with only the Bottlenose plugged in. If the upload can't connect, hold BOOT, tap RESET, release BOOT, and retry.
4. **Watch it start** (optional): `arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200`
   ```
   [i2c] looking for the OG (0x42) on every likely pin pair...
   [i2c] OG found: SDA=GPIO6 SCL=GPIO7 (saved)
   [wifi] connected, IP 192.168.1.42
   [tcp] listening on forklift-01.local:5000
   ```
   The Qwiic pins aren't published, so the firmware finds them on first boot by trying each likely pair until the OG answers, then remembers them. The OG must be on, running the forklift firmware, with the Qwiic cable connected.

## 4. Drive over WiFi

When the Bottlenose joins the network, **the OG's screen shows its IP address** under the state. On the laptop (same network):

```bash
export FORKLIFT_PORT=socket://forklift-01.local:5000     # or socket://<IP on the screen>:5000
uv run python -m fleet_edge.step5_serial_monitor          # T,IDLE,0.00 every second = working
uv run python -m fleet_edge.step6_ring1                    # everything else works the same
```

Unset `FORKLIFT_PORT` to go back to USB. `forklift-01.local` needs mDNS (avahi on Linux, built in on macOS). If it doesn't resolve, use the IP.

## Safety

| Failure | What stops the motors |
|---|---|
| Laptop program quits or the TCP connection drops | Bottlenose sends `S,wifi_lost` immediately |
| Laptop freezes, WiFi drops out, Bottlenose crashes or loses power | The OG's 500 ms watchdog (no command → brake) |
| A second laptop connects | The first is dropped and `S,replaced` is sent |

**No authentication:** anyone on the same WiFi network can connect to port 5000 and drive the forklift. That's fine on a home or lab network. Don't use it on shared or public WiFi.

## Notes

- Latency: WiFi power save is turned off (it adds 100+ ms). Expect roughly 5–20 ms on a quiet network, compared with about 1 ms over USB.
- The OG runs from its internal battery when the USB cable is unplugged. It charges whenever USB is connected.
