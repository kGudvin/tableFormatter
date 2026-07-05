from decimal import Decimal
from typing import Any, cast

from app.domain.models import OfficialResult, ResultStatus
from app.services.processor import ProcurementProcessor, _json_safe
from app.sheets.schema import find_header_row


def test_row_needs_autofill_until_winner_and_products_are_filled() -> None:
    schema = find_header_row(
        [
            [
                "Номер аукциона",
                "Дата окончания приёма заявок",
                "Объект закупки",
                "Статус",
                "Победная ставка",
                "Кто выиграл",
                "Поставляемый товар",
            ]
        ]
    )
    processor = ProcurementProcessor(
        source=_Dummy(),
        sheets=_Dummy(),
        db=cast(Any, _Dummy()),
        spreadsheet_id="spreadsheet",
        sheet_name="2026 (44)",
    )

    assert not processor._row_needs_autofill(
        ["012345678901", "", "", "", "100", "ООО Ромашка", "Монитор"],
        schema,
    )
    assert processor._row_needs_autofill(["012345678901", "", "", "", "100", "", "Монитор"], schema)
    assert processor._row_needs_autofill(["012345678901", "", "", "", "100", "ООО Ромашка", ""], schema)
    assert processor._row_needs_autofill(["012345678901", "", "", "", "100", "", ""], schema)


def test_json_safe_converts_decimal_values() -> None:
    assert _json_safe({"old": "2 528 225,99", "new": Decimal("2196655.19")}) == {
        "old": "2 528 225,99",
        "new": "2196655.19",
    }


def test_sheet_values_convert_decimals_to_strings() -> None:
    processor = ProcurementProcessor(
        source=_Dummy(),
        sheets=_Dummy(),
        db=cast(Any, _Dummy()),
        spreadsheet_id="spreadsheet",
        sheet_name="2026 (44)",
    )
    values = processor._sheet_values(
        OfficialResult(
            purchase_number="012345678901",
            winner_name="ООО Ромашка",
            winner_inn="7707083893",
            winning_price=Decimal("2196655.19"),
            current_contract_price=Decimal("2000000.00"),
            products=[],
            status=ResultStatus.CONFIRMED,
        )
    )

    assert values["Победная ставка"] == "2196655.19"
    assert values["Текущая цена контракта"] == "2000000.00"


class _Dummy:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unexpected test access: {name}")
