from decimal import Decimal

from app.domain.models import ProductItem
from app.domain.products import format_product_item, format_products


def test_format_one_product() -> None:
    item = ProductItem(
        position=1,
        country="Россия",
        manufacturer="ООО Ромашка",
        trademark="Romashka",
        model="A-10",
        registry_number="123/45",
        quantity=Decimal("10"),
        unit="шт",
    )
    assert format_product_item(item) == (
        "страна: Россия; производитель/ТМ: ООО Ромашка / Romashka; "
        "модель: A-10; реестровый номер: 123/45; количество: 10 шт"
    )


def test_format_multiple_products_with_line_breaks() -> None:
    text = format_products(
        [
            ProductItem(position=2, manufacturer="B"),
            ProductItem(position=1, manufacturer="A", quantity=Decimal("2.50"), unit="упак"),
        ]
    )
    assert text == "1. производитель: A; количество: 2.5 упак\n2. производитель: B"


def test_format_missing_product_fields() -> None:
    assert format_product_item(ProductItem(position=1)) == "-"

