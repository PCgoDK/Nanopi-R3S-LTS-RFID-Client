import struct
from datetime import datetime, timezone

import pytest

from app.llrp import build_tag_report


def test_builds_report_with_epc_and_optional_reader_fields():
    time = datetime(1970, 1, 1, 0, 0, 1, 250000, tzinfo=timezone.utc)
    frame = build_tag_report("ab 12", 7, antenna=1, rssi=-61, timestamp=time)

    message_type, message_length, message_id = struct.unpack("!HII", frame[:10])
    assert (message_type, message_length, message_id) == (0x0400 | 61, len(frame), 7)
    tag_type, tag_length = struct.unpack("!HH", frame[10:14])
    assert (tag_type, tag_length) == (240, len(frame) - 10)
    epc_type, epc_length, bit_length = struct.unpack("!HHH", frame[14:20])
    assert (epc_type, epc_length, bit_length) == (241, 8, 16)
    assert frame[20:22] == b"\xab\x12"
    assert frame[22:] == (
        b"\x81\x00\x01" + b"\x86\xc3" + b"\x82" + struct.pack("!Q", 1_250_000)
    )


@pytest.mark.parametrize("epc", ["NoTag", "", "ABC", "AABB" * 33])
def test_rejects_non_epc_values(epc):
    with pytest.raises(ValueError):
        build_tag_report(epc, 1)


def test_rejects_naive_timestamp_and_invalid_reader_fields():
    with pytest.raises(ValueError):
        build_tag_report("AB12", 1, timestamp=datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        build_tag_report("AB12", 1, rssi=-129)
    with pytest.raises(ValueError):
        build_tag_report("AB12", 1, antenna=0)
