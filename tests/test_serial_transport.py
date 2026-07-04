"""Frame-level tests for SerialTransport, against pyserial's loop:// port.

loop:// is a virtual, in-process serial port that echoes every write back to
its own reads - it is not a simulated device. These tests only check the
transport's wire framing (newline-terminated lines) and its timeout
behavior; they do NOT exercise the DUT protocol over this port.
"""

import pytest

from testbench.serial_transport import SerialTransport, TransportTimeout


def test_written_line_reads_back_intact():
    transport = SerialTransport("loop://", timeout=1.0)
    try:
        assert transport.request("GET_STATE") == "GET_STATE"
    finally:
        transport.close()


def test_read_times_out_when_nothing_is_written():
    transport = SerialTransport("loop://", timeout=0.1)
    try:
        with pytest.raises(TransportTimeout):
            transport._read_line()
    finally:
        transport.close()
