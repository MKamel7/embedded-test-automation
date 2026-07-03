"""Device driver used by the test bench.

The driver talks to the device through a Transport, so the same test suite
runs against the simulator (SimTransport) today and a real controller over
UART (a pyserial-backed Transport) tomorrow - the HIL upgrade path.
"""

from typing import Protocol

from dut_sim.motor_controller import MotorControllerSim


class Transport(Protocol):
    def request(self, line: str) -> str:
        """Send one command line, return one response line."""


class SimTransport:
    """In-process transport bound to a MotorControllerSim instance."""

    def __init__(self, sim: MotorControllerSim, steps_per_request: int = 1):
        self.sim = sim
        # Physics advances with every exchange, like time passing on a bench.
        self.steps_per_request = steps_per_request

    def request(self, line: str) -> str:
        response = self.sim.handle_command(line)
        self.sim.step(self.steps_per_request)
        return response


class ProtocolError(Exception):
    """Raised when the device answers ERR or something unparseable."""


class MotorControllerDriver:

    def __init__(self, transport: Transport):
        self.transport = transport

    def _query(self, command: str) -> str:
        response = self.transport.request(command)
        if not response.startswith("OK"):
            raise ProtocolError(f"{command!r} -> {response!r}")
        return response[2:].strip()

    # ---- typed API -----------------------------------------------------
    def set_speed(self, rpm: float) -> None:
        self._query(f"SET_SPEED {rpm}")

    def get_speed(self) -> float:
        return float(self._query("GET_SPEED"))

    def get_temperature(self) -> float:
        return float(self._query("GET_TEMP"))

    def get_state(self) -> str:
        return self._query("GET_STATE")

    def stop(self) -> None:
        self._query("STOP")

    def reset(self) -> None:
        self._query("RESET")
