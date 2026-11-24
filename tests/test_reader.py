import time

import pytest

from app.main import ReaderService
from app.protocol import build_frame


class FakeSerial:
    def __init__(self, response=b""):
        self.is_open = True
        self.response = bytearray(response)
        self.writes = []

    def read(self, size):
        if self.response:
            result = bytes(self.response[:size])
            del self.response[:size]
            return result
        time.sleep(0.002)
        return b""

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def close(self):
        self.is_open = False


def test_send_records_command_response_and_counters():
    response = build_frame(0x03, b"M100 V1.0", frame_type=0x01)
    service = ReaderService()
    service._serial = FakeSerial(response)
    service._port = "/dev/ttyUSB0"
    service._baud = 115200

    command = build_frame(0x03, b"\x00")
    responses = service.send(command, timeout=0.1)

    assert responses == [response.hex(" ").upper()]
    state = service.state()
    assert state["tx_bytes"] == len(command)
    assert state["rx_bytes"] == len(response)
    assert state["rx_frames"] == 1
    assert state["events"] == ["TX " + command.hex(" ").upper(), "RX " + response.hex(" ").upper()]


def test_continuous_inventory_sends_start_and_stop_commands():
    fake = FakeSerial()
    service = ReaderService()
    service._serial = fake
    service._port = "/dev/ttyUSB0"
    service._baud = 115200

    service.start_inventory(0x1234)
    assert fake.writes == [build_frame(0x27, bytes.fromhex("22 12 34"))]

    service.stop_inventory()
    assert fake.writes[-1] == build_frame(0x28)
    assert not service.inventory_running


def test_read_frame_accepts_end_marker_inside_payload():
    response = build_frame(0x03, b"M100\x7eV1", frame_type=0x01)
    service = ReaderService()
    fake = FakeSerial(response)

    assert service._read_frame(fake, 0.1) == response


def test_read_frame_keeps_incomplete_frame_for_next_read():
    response = build_frame(0x08, b"\x03", frame_type=0x01)
    service = ReaderService()
    fake = FakeSerial(response[:4])

    assert service._read_frame(fake, 0.01) is None
    fake.response.extend(response[4:])
    assert service._read_frame(fake, 0.1) == response


def test_sniffer_inventory_records_tag_notifications_and_blocks_manual_stop():
    tag = build_frame(0x22, bytes.fromhex("D0 08 00 AB CD 12 34"), frame_type=0x02)
    invalid_pc = build_frame(0x22, bytes.fromhex("D0 00 00 AB CD 12 34"), frame_type=0x02)
    command_reply = build_frame(0x22, bytes.fromhex("D0 08 00 AB CD 12 34"), frame_type=0x01)
    service = ReaderService()
    service._serial = FakeSerial(command_reply + invalid_pc + tag)
    observations = []
    service.observation_callback = lambda epc, timestamp: observations.append(epc)
    service.start_inventory(65535, owner="sniffer")
    deadline = time.monotonic() + 1
    while not observations and time.monotonic() < deadline:
        time.sleep(0.01)
    assert observations == ["ABCD"]
    with pytest.raises(RuntimeError):
        service.stop_inventory(owner="manual")
    service.stop_inventory(owner="sniffer")
    assert not service.inventory_running


@pytest.mark.parametrize("code, expected_error", [(0x15, False), (0x16, True)])
def test_sniffer_inventory_surfaces_reader_errors_without_treating_no_tag_as_failure(code, expected_error):
    response = build_frame(0xFF, bytes([code]), frame_type=0x01)
    service = ReaderService()
    service._serial = FakeSerial(response)
    service.start_inventory(65535, owner="sniffer")
    try:
        deadline = time.monotonic() + 1
        while not service._inventory_error and not service._inventory_response_received and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bool(service._inventory_error) == expected_error
        assert service._inventory_response_received != expected_error
    finally:
        service.stop_inventory(owner="sniffer")
