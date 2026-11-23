import pytest

from app.protocol import ProtocolError, build_frame, parse_frame, parse_hex_frame, tag_reading


def test_builds_m5stack_hardware_version_command():
    assert build_frame(0x03, b"\x00").hex(" ").upper() == "BB 00 03 00 01 00 04 7E"


def test_parses_frame_and_rejects_bad_checksum():
    raw = build_frame(0x08)
    parsed = parse_frame(raw)

    assert parsed.frame_type == 0
    assert parsed.command == 0x08
    assert parsed.payload == b""

    with pytest.raises(ProtocolError, match="checksum"):
        parse_hex_frame("BB 00 08 00 00 00 7E")


def test_parses_tag_inventory_frame():
    payload = bytes.fromhex("CD 34 00 31 D5 5B E6 80 00 02 15 6D 00 00 00 77 EB")
    parsed = parse_frame(build_frame(0x22, payload, frame_type=0x02))

    assert tag_reading(parsed) == {
        "pc": "34 00",
        "epc": "31D55BE6800002156D000000",
        "crc": "77 EB",
        "rssi_dbm": -51,
    }
