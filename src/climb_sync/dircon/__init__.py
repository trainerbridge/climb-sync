"""DIRCON (Wahoo Direct Connect) transport — asyncio port of spike 004."""
from .client import DirconClient, with_reconnect
from .codec import (
    encode_frame, decode_header,
    encode_grade, encode_target_power,
    uuid_bytes,
    FTMS_CP, WAHOO_CLIMB,
    OP_ENUM, OP_GET_CHARS, OP_READ, OP_WRITE, OP_SUBSCRIBE, OP_NOTIFY,
    FTMS_REQUEST_CONTROL, FTMS_RESET, FTMS_SET_TARGET_POWER, FTMS_START,
)
from .discovery import discover_kickr, SERVICE_TYPE

__all__ = [
    "DirconClient", "with_reconnect",
    "encode_frame", "decode_header",
    "encode_grade", "encode_target_power", "uuid_bytes",
    "FTMS_CP", "WAHOO_CLIMB",
    "OP_ENUM", "OP_GET_CHARS", "OP_READ", "OP_WRITE", "OP_SUBSCRIBE", "OP_NOTIFY",
    "FTMS_REQUEST_CONTROL", "FTMS_RESET", "FTMS_SET_TARGET_POWER", "FTMS_START",
    "discover_kickr", "SERVICE_TYPE",
]
