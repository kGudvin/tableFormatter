import re
from hashlib import sha256

_WS_RE = re.compile(r"\s+")


def normalize_field_value(value: object) -> str:
    if value is None:
        return ""
    return _WS_RE.sub(" ", str(value).strip()).casefold()


def field_hash(value: object) -> str:
    return sha256(normalize_field_value(value).encode("utf-8")).hexdigest()


def is_manual_change(current_value: object, last_auto_value: object) -> bool:
    if current_value in (None, "") and last_auto_value in (None, ""):
        return False
    return field_hash(current_value) != field_hash(last_auto_value)

