import re
from decimal import Decimal, InvalidOperation

_MONEY_RE = re.compile(r"[-+]?\d[\d\s\u00a0]*(?:[,.]\d+)?")


def parse_money(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    text = str(value).strip()
    if not text or text == "-":
        return None
    match = _MONEY_RE.search(text.replace("₽", "").replace("руб.", ""))
    if not match:
        return None
    normalized = match.group(0).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None

