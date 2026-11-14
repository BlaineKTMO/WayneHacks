"""STEP 5 (debug helper): print every line the Free-WiLi sends, and send your own lines.

  uv run python -m fleet_edge.step5_serial_monitor

Type a line like  W,40,40  or  S,test  and press Enter. Ctrl+C to quit.
Use this when step4 doesn't work, to see whether the Free-WiLi is hearing you at all.
"""

import sys
import threading

import serial

from . import config


def main():
    ser = serial.Serial(config.SERIAL_PORT, config.SERIAL_BAUD, timeout=0.1)
    print(f"connected to {config.SERIAL_PORT}. Type lines to send.")

    def reader():
        while True:
            line = ser.readline()
            if line:
                print(f"<< {line.decode(errors='replace').rstrip()}")

    threading.Thread(target=reader, daemon=True).start()
    try:
        for line in sys.stdin:
            ser.write((line.strip() + "\n").encode())
            print(f">> {line.strip()}")
    except KeyboardInterrupt:
        pass
    finally:
        ser.write(b"S,monitor_exit\n")
        ser.close()


if __name__ == "__main__":
    main()
