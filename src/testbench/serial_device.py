"""Serves the simulated DUT over a real serial port: the other half of HIL.

WHY THIS EXISTS. `SerialTransport` is documented as the hardware-in-the-loop
upgrade path, and until now the only thing that had ever driven it was
pyserial's `loop://`, which echoes writes back to the same in-process buffer.
`loop://` proves the framing and nothing else: there is no device on the far
end, so `request("GET_STATE")` answers `"GET_STATE"` rather than `"OK IDLE"`,
no kernel tty is involved, and no byte ever crosses a file descriptor. A
transport whose only exercise is an echo has not been shown to talk to
anything.

This class is the missing counterpart. Point it at one end of a PTY pair and
`SerialTransport` at the other, and the full stack runs over a real character
device: driver, framing, kernel tty layer, and a DUT that answers the protocol
instead of parroting it. No board and no driver install, which is what makes it
worth doing now rather than when hardware arrives.

WHAT IT STILL IS NOT. A PTY has no baud rate, no parity, no framing errors and
no electrical noise, so this closes the "is anything on the other end" gap and
leaves the "does it survive a real UART" gap open. `docs/TEST_STRATEGY.md` says
which is which; the honest limit is that this proves the software path only.

DECODING IS DELIBERATELY TOTAL. Real lines corrupt bytes, so decoding uses
`errors="replace"` rather than raising: a mangled command becomes an unknown
command and the DUT answers `ERR UNKNOWN`, which is what a device does. Raising
here would turn a line fault into a server crash.
"""

from __future__ import annotations

import serial

from dut_sim.motor_controller import MotorControllerSim


class SerialDeviceServer:
    """A `MotorControllerSim` bound to a serial port, answering one line at a time."""

    def __init__(
        self,
        port: str,
        sim: MotorControllerSim,
        steps_per_request: int = 1,
        timeout: float = 0.1,
    ) -> None:
        self.sim = sim
        # Physics advances with every exchange, matching SimTransport so the
        # two transports are comparable rather than subtly different devices.
        self.steps_per_request = steps_per_request
        self.serial = serial.serial_for_url(port, timeout=timeout)
        self._running = False

    def serve_once(self) -> bool:
        """Answer one command line. False means the read timed out with no full line.

        The timeout is the stop signal: `serve_forever` cannot check a flag
        while blocked in `readline`, so the read has to return on its own.
        """
        raw: bytes = self.serial.readline()
        if not raw.endswith(b"\n"):
            return False

        command = raw.decode("ascii", errors="replace").strip()
        response = self.sim.handle_command(command)
        self.sim.step(self.steps_per_request)
        self.serial.write((response + "\n").encode("ascii"))
        self.serial.flush()
        return True

    def serve_forever(self) -> None:
        """Answer commands until `stop()`. Intended to run in a worker thread."""
        self._running = True
        while self._running:
            self.serve_once()

    def stop(self) -> None:
        """Ask `serve_forever` to return, within one read timeout."""
        self._running = False

    def close(self) -> None:
        self.serial.close()
