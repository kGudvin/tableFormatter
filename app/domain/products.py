from decimal import Decimal

from app.domain.models import ProductItem


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        return text.rstrip("0").rstrip(".")
    return text


def format_product_item(item: ProductItem) -> str:
    parts: list[str] = []
    if item.country:
        parts.append(f"страна: {item.country}")
    manufacturer = _clean(item.manufacturer)
    trademark = _clean(item.trademark)
    if manufacturer and trademark and manufacturer.lower() != trademark.lower():
        parts.append(f"производитель/ТМ: {manufacturer} / {trademark}")
    elif manufacturer:
        parts.append(f"производитель: {manufacturer}")
    elif trademark:
        parts.append(f"ТМ: {trademark}")
    if item.model:
        parts.append(f"модель: {item.model}")
    if item.registry_number:
        parts.append(f"реестровый номер: {item.registry_number}")
    if item.quantity is not None:
        quantity = _format_decimal(item.quantity)
        parts.append(f"количество: {quantity} {item.unit or ''}".strip())
    return "; ".join(parts) if parts else "-"


def format_products(items: list[ProductItem]) -> str:
    if not items:
        return "-"
    lines = []
    for item in sorted(items, key=lambda p: p.position):
        prefix = f"{item.position}. " if len(items) > 1 else ""
        lines.append(prefix + format_product_item(item))
    return "\n".join(lines)
