"""Compile and run the firmware's pure-C protocol logic on the laptop (no Pico SDK needed)."""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


@pytest.mark.skipif(CC is None, reason="no C compiler on PATH")
def test_motor_proto(tmp_path):
    exe = tmp_path / "test_motor_proto"
    subprocess.run(
        [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(exe),
         str(HERE / "test_motor_proto.c"), str(HERE.parent / "main" / "motor_proto.c"),
         str(HERE.parent / "main" / "byte_ring.c"),
         str(HERE.parent / "main" / "rf_proto.c")],
        check=True,
    )
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_python_and_firmware_agree():
    """freewili_link.py must speak exactly what motor_proto.c parses."""
    from fleet_edge.freewili_link import format_fork, format_stop, format_wheels, parse_line

    assert format_wheels(40, -25) == "W,40,-25\n"
    assert format_wheels(250, -250) == "W,100,-100\n"
    assert format_stop("lost,tag") == "S,lost tag\n"
    assert format_fork(60, 1500) == "F,60,1500\n"
    assert format_fork(-250, 99999) == "F,-100,5000\n"   # same caps as the firmware
    assert format_fork(30, -5) == "F,30,0\n"
    t = parse_line("T,DRIVING,0.00")
    assert t is not None and t.state == "DRIVING" and t.battery_v == 0.0
    assert parse_line("[forklift] breakout ready") is None   # firmware DIAG lines are ignored
