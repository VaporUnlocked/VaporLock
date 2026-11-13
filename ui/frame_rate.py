"""Frame-rate limits shared by the profile, editor and native command bridge."""

MAX_FRAME_RATE = 2_147_483_647  # Matches the native engine's signed int.
FRAME_RATE_FIELDS = frozenset(("capture_fps", "inference_fps"))


def valid_frame_rate(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_FRAME_RATE
