"""
DIRCON (Wahoo Direct Connect) wire-format codec — pure functions only.

Ported byte-for-byte from .planning/spikes/004-replay-from-bleak/dircon_client.py
lines 39-134. The wire format is frozen (D-04): every byte produced here was
validated on real KICKR hardware 2026-04-24 against SYSTM's own DIRCON capture.

Frame format (all observed messages):
    [01] [opcode] [seq_lo seq_hi] [len_hi len_lo] [payload]

Header: 6 bytes. Sequence number is little-endian. Length is BIG-endian
and covers payload bytes only.
"""
from __future__ import annotations

import uuid as uuidlib


def uuid_bytes(u) -> bytes:
    """Convert a UUID string or short 16-bit int into 16 raw big-endian bytes."""
    if isinstance(u, int):
        full = f"0000{u:04x}-0000-1000-8000-00805f9b34fb"
        return uuidlib.UUID(full).bytes
    if isinstance(u, str):
        return uuidlib.UUID(u).bytes
    if isinstance(u, (bytes, bytearray)) and len(u) == 16:
        return bytes(u)
    raise ValueError(f"uuid_bytes: unsupported type {type(u)}")


# --- UUID constants (D-04 locked) ---
FTMS_CP = uuid_bytes(0x2AD9)
WAHOO_CLIMB = uuid_bytes("a026e037-0a7d-4ab3-97fa-f1500f9feb8b")

# --- FTMS sub-opcodes ---
FTMS_REQUEST_CONTROL = 0x00
FTMS_RESET = 0x01
FTMS_SET_TARGET_POWER = 0x05
FTMS_START = 0x07

# --- DIRCON frame opcodes ---
OP_ENUM = 0x01
OP_GET_CHARS = 0x02
OP_READ = 0x03
OP_WRITE = 0x04
OP_SUBSCRIBE = 0x05
OP_NOTIFY = 0x06

OPCODE_NAMES = {
    OP_ENUM: "Enumerate services",
    OP_GET_CHARS: "Get characteristics",
    OP_READ: "Read characteristic",
    OP_WRITE: "Write characteristic",
    OP_SUBSCRIBE: "Subscribe",
    OP_NOTIFY: "Notification/Response",
}


def encode_frame(opcode: int, seq: int, payload: bytes = b"") -> bytes:
    """Build the 6-byte DIRCON header + payload. Sequence is little-endian; length is big-endian."""
    header = bytes([
        0x01,
        opcode,
        seq & 0xFF, (seq >> 8) & 0xFF,
        (len(payload) >> 8) & 0xFF, len(payload) & 0xFF,
    ])
    return header + payload


def decode_header(hdr6: bytes):
    """Parse the 6-byte DIRCON frame header. Returns (msg_type, opcode, seq, payload_len) or None."""
    if len(hdr6) < 6:
        return None
    msg_type = hdr6[0]
    opcode = hdr6[1]
    seq = hdr6[2] | (hdr6[3] << 8)
    length = (hdr6[4] << 8) | hdr6[5]
    return (msg_type, opcode, seq, length)


def encode_grade(grade_fraction: float) -> bytes:
    """
    Encode Wahoo Climb grade command payload for char a026e037.
    Input:  grade_fraction in the range [-1.0, 1.0] (e.g. 0.06 = 6% grade)
    Output: 3-byte payload `66 [grade_lo] [grade_hi]` where grade = round(fraction * 10000).
    Observed values: 66 00 00 = 0%, 66 58 02 = 6% (0x0258 = 600 = 6.00%).
    """
    clamped = max(-1.0, min(1.0, grade_fraction))
    hundredths = int(round(clamped * 10000))  # signed int16 range
    hundredths = max(-32768, min(32767, hundredths))
    lo = hundredths & 0xFF
    hi = (hundredths >> 8) & 0xFF
    return bytes([0x66, lo, hi])


def encode_target_power(watts: int) -> bytes:
    """Encode FTMS Set Target Power payload. Returns 3 bytes: 05 [lo] [hi] (int16 LE watts)."""
    w = max(0, min(watts, 65535))
    return bytes([FTMS_SET_TARGET_POWER, w & 0xFF, (w >> 8) & 0xFF])
