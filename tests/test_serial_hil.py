"""The pyserial path, exercised end to end over a real PTY pair.

WHAT THIS ADDS OVER `test_serial_transport.py`. That file drives
`SerialTransport` against pyserial's `loop://`, which is an in-process echo:
useful for framing, but there is no device on the far end and no kernel tty in
the middle, so `request("GET_SPEED")` comes back as `"GET_SPEED"`. Here a real
`MotorControllerSim` answers over a real character device, so the assertions
are about the protocol (`OK 1500`) rather than about an echo, and every byte
crosses a file descriptor and the kernel's tty layer on the way.

The pair comes from `socat -d -d pty,raw,echo=0 pty,raw,echo=0`, which is the
whole reason this became possible on Linux: on Windows the equivalent needed
the com0com driver, a system install, which is why the README carried "a
virtual COM port" as future work for months. Here it is one process and no
install. `raw` matters and is not decoration: without it the tty runs in
canonical mode and does its own line editing, and `echo=0` stops each end
hearing itself.

WHAT IS STILL NOT PROVEN. A PTY has no baud rate, no parity, no stop bits and
no noise. This closes the gap between "the transport frames lines" and "the
transport talks to a device"; it does not close the gap to a real UART. The
remaining step needs a board.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from dut_sim.motor_controller import MotorControllerSim
from testbench.driver import MotorControllerDriver, ProtocolError
from testbench.serial_device import SerialDeviceServer
from testbench.serial_transport import SerialTransport

ROOT = Path(__file__).resolve().parent.parent
PTY_ANNOUNCEMENT = re.compile(r"PTY is (\S+)")
SOCAT_STARTUP_TIMEOUT_S = 5.0


@pytest.fixture()
def pty_pair() -> Iterator[tuple[str, str]]:
    """Two linked PTY device paths, from socat, torn down with the test.

    socat announces each end on stderr as "N PTY is /dev/pts/4", so the paths
    are read back rather than guessed: /dev/pts numbering is whatever the
    kernel hands out, and hardcoding one would collide with any other terminal.
    """
    socat = shutil.which("socat")
    if socat is None:
        pytest.skip("socat is not installed; see test_ci_installs_socat")

    process = subprocess.Popen(
        [socat, "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
        stderr=subprocess.PIPE,
    )
    assert process.stderr is not None

    paths: list[str] = []
    deadline = time.monotonic() + SOCAT_STARTUP_TIMEOUT_S
    while len(paths) < 2 and time.monotonic() < deadline:
        announcement = process.stderr.readline()
        if not announcement:            # socat died before announcing both ends
            break
        found = PTY_ANNOUNCEMENT.search(announcement.decode("ascii", errors="replace"))
        if found:
            paths.append(found.group(1))

    try:
        assert len(paths) == 2, f"socat announced {len(paths)} PTYs, expected 2"
        yield paths[0], paths[1]
    finally:
        process.terminate()
        process.wait(timeout=SOCAT_STARTUP_TIMEOUT_S)
        process.stderr.close()


@pytest.fixture()
def hil(pty_pair: tuple[str, str]) -> Iterator[tuple[MotorControllerDriver, MotorControllerSim]]:
    """A driver on one end of the pair, the simulated device serving the other."""
    device_port, host_port = pty_pair
    sim = MotorControllerSim()
    server = SerialDeviceServer(device_port, sim)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    transport = SerialTransport(host_port, timeout=2.0)
    try:
        yield MotorControllerDriver(transport), sim
    finally:
        transport.close()
        server.stop()
        worker.join(timeout=SOCAT_STARTUP_TIMEOUT_S)
        assert not worker.is_alive(), "the device server did not stop within its read timeout"
        server.close()


# --- the protocol, over a real character device ------------------------------

def test_device_answers_the_protocol_and_not_an_echo(
    hil: tuple[MotorControllerDriver, MotorControllerSim],
) -> None:
    """The assertion `loop://` cannot make: a reply that differs from the request."""
    dut, _ = hil
    assert dut.get_state() == "IDLE"


def test_speed_command_round_trips_over_the_wire(
    hil: tuple[MotorControllerDriver, MotorControllerSim],
) -> None:
    dut, _ = hil
    dut.set_speed(1500)
    assert dut.get_state() == "RUNNING"
    dut.stop()
    assert dut.get_state() == "IDLE"


def test_a_latched_fault_survives_the_serial_path(
    hil: tuple[MotorControllerDriver, MotorControllerSim],
) -> None:
    """Fault latching is DUT behaviour, so it must look identical over serial."""
    dut, sim = hil
    sim.trip_fault("OVERHEAT")

    assert dut.get_state() == "FAULT"
    with pytest.raises(ProtocolError):
        dut.set_speed(1000)


def test_an_out_of_range_speed_is_refused_over_the_wire(
    hil: tuple[MotorControllerDriver, MotorControllerSim],
) -> None:
    """An ERR reply has to survive framing too, not only an OK one."""
    dut, _ = hil
    with pytest.raises(ProtocolError):
        dut.set_speed(999_999)


# --- the server's own edges --------------------------------------------------

def test_serve_once_reports_a_timeout_when_no_command_arrives(
    pty_pair: tuple[str, str],
) -> None:
    """The read timeout is what lets `serve_forever` notice `stop()`.

    Asserted directly, because if this returned True on an empty read the
    server would spin instead of blocking and nothing else here would notice.
    """
    device_port, _ = pty_pair
    server = SerialDeviceServer(device_port, MotorControllerSim(), timeout=0.05)
    try:
        assert server.serve_once() is False
    finally:
        server.close()


def test_corrupt_bytes_become_an_unknown_command_rather_than_a_crash(
    pty_pair: tuple[str, str],
) -> None:
    """A line fault must not take the device down.

    Non-ASCII arrives on a real link. Decoding is total by design, so the
    mangled command reaches the DUT and is answered `ERR UNKNOWN`, which is
    what the protocol says an unrecognised command gets.
    """
    device_port, host_port = pty_pair
    server = SerialDeviceServer(device_port, MotorControllerSim())
    transport = SerialTransport(host_port, timeout=2.0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        transport.serial.write(b"\xff\xfe GET_STATE\n")
        transport.serial.flush()
        assert transport._read_line() == "ERR UNKNOWN"
    finally:
        transport.close()
        server.stop()
        worker.join(timeout=SOCAT_STARTUP_TIMEOUT_S)
        server.close()


# --- the gate over the gate --------------------------------------------------

def test_ci_installs_socat() -> None:
    """Without this, the whole file above can skip forever and look green.

    `pty_pair` skips when socat is missing, which is right on a developer
    machine and wrong in CI: a suite that silently stops running its only
    hardware-path tests is the exact failure this repository argues against
    (`docs/TEST_STRATEGY.md`, and the same reasoning as
    `test_the_readme_states_the_count_at_all`). So the skip is allowed only
    because something asserts the tests really do run somewhere.
    """
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "socat" in workflow, (
        "CI does not install socat, so the serial HIL tests skip there and "
        "this file guards nothing"
    )
