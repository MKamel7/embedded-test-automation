"""pyserial-backed Transport: the HIL upgrade path.

Same line-based protocol as SimTransport, but over an actual (or virtual)
serial port via pyserial, so the same test suite can run against real
hardware. Commands are ASCII lines terminated by "\n"; a response is read
back the same way, with a configurable timeout.
"""

import serial


class TransportTimeout(Exception):
    """Raised when no response line arrives within the configured timeout."""


class SerialTransport:
    """Transport bound to a pyserial connection (real UART or virtual port)."""

    def __init__(self, port: str, timeout: float = 1.0):
        self.timeout = timeout
        self.serial = serial.serial_for_url(port, timeout=timeout)

    def request(self, line: str) -> str:
        self.serial.write((line + "\n").encode("ascii"))
        return self._read_line(context=line)

    def _read_line(self, context: str = "") -> str:
        """Read one '\\n'-terminated line, raising TransportTimeout if none arrives."""
        raw = self.serial.readline()
        if not raw.endswith(b"\n"):
            detail = f" for {context!r}" if context else ""
            raise TransportTimeout(f"no response within {self.timeout}s{detail}")
        return raw.decode("ascii").strip()

    def close(self) -> None:
        self.serial.close()
