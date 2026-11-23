import re
import struct
from datetime import datetime, timezone


def build_tag_report(
    epc: str,
    message_id: int,
    antenna: int | None = None,
    rssi: int | None = None,
    timestamp: datetime | None = None,
) -> bytes:
    epc = epc.strip().replace(" ", "").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}){1,64}", epc):
        raise ValueError("A valid hexadecimal EPC is required; NoTag cannot be sent.")
    if not isinstance(message_id, int) or isinstance(message_id, bool) or not 0 <= message_id <= 0xFFFFFFFF:
        raise ValueError("LLRP message ID must fit in an unsigned 32-bit integer.")

    epc_bytes = bytes.fromhex(epc)
    epc_body = struct.pack("!H", len(epc_bytes) * 8) + epc_bytes
    tag_data = struct.pack("!HH", 241, len(epc_body) + 4) + epc_body
    if antenna is not None:
        if not isinstance(antenna, int) or isinstance(antenna, bool) or not 1 <= antenna <= 0xFFFF:
            raise ValueError("Antenna ID must be between 1 and 65535.")
        tag_data += struct.pack("!BH", 0x81, antenna)
    if rssi is not None:
        if not isinstance(rssi, int) or isinstance(rssi, bool) or not -128 <= rssi <= 127:
            raise ValueError("RSSI must fit in a signed byte.")
        tag_data += struct.pack("!Bb", 0x86, rssi)
    if timestamp is not None:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone.")
        delta = timestamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        if not 0 <= micros <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Timestamp is outside the LLRP range.")
        tag_data += struct.pack("!BQ", 0x82, micros)

    report = struct.pack("!HH", 240, len(tag_data) + 4) + tag_data
    return struct.pack("!HII", (1 << 10) | 61, len(report) + 10, message_id) + report
