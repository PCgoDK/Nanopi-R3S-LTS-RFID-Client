from dataclasses import dataclass


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    frame_type: int
    command: int
    payload: bytes
    raw: bytes


def build_frame(command: int, payload: bytes = b"", frame_type: int = 0) -> bytes:
    if not 0 <= command <= 0xFF:
        raise ProtocolError("Command must fit in one byte.")
    if not 0 <= frame_type <= 0xFF:
        raise ProtocolError("Frame type must fit in one byte.")
    if len(payload) > 0xFFFF:
        raise ProtocolError("Payload is too long.")

    frame = bytearray((0xBB, frame_type, command, len(payload) >> 8, len(payload) & 0xFF))
    frame.extend(payload)
    frame.append(sum(frame[1:]) & 0xFF)
    frame.append(0x7E)
    return bytes(frame)


def parse_frame(raw: bytes) -> Frame:
    if len(raw) < 7 or raw[0] != 0xBB or raw[-1] != 0x7E:
        raise ProtocolError("Invalid frame boundaries.")

    payload_length = int.from_bytes(raw[3:5], "big")
    if len(raw) != payload_length + 7:
        raise ProtocolError("Frame length does not match its payload length.")
    if (sum(raw[1:-2]) & 0xFF) != raw[-2]:
        raise ProtocolError("Invalid frame checksum.")

    return Frame(raw[1], raw[2], raw[5:-2], raw)


def parse_hex_frame(value: str) -> Frame:
    compact = "".join(value.split())
    if len(compact) % 2:
        raise ProtocolError("Hex input must contain complete bytes.")
    try:
        raw = bytes.fromhex(compact)
    except ValueError as exc:
        raise ProtocolError("Hex input contains invalid characters.") from exc
    return parse_frame(raw)


def tag_reading(frame: Frame) -> dict | None:
    if frame.command != 0x22 or len(frame.payload) < 5:
        return None

    rssi = int.from_bytes(frame.payload[0:1], "big", signed=True)
    pc = frame.payload[1:3]
    epc_length = ((int.from_bytes(pc, "big") >> 11) & 0x1F) * 2
    if epc_length == 0:
        epc_length = len(frame.payload) - 5
    if epc_length < 0 or len(frame.payload) < 3 + epc_length + 2:
        return None

    epc_start = 3
    epc = frame.payload[epc_start:epc_start + epc_length]
    crc = frame.payload[epc_start + epc_length:epc_start + epc_length + 2]
    return {
        "pc": pc.hex(" ").upper(),
        "epc": epc.hex().upper(),
        "crc": crc.hex(" ").upper(),
        "rssi_dbm": rssi,
    }
