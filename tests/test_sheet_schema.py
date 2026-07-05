from app.sheets.schema import (
    SERVICE_COLUMNS,
    find_header_row,
    is_purchase_number,
    is_working_row,
    normalize_purchase_number,
)


def test_find_header_row_and_columns() -> None:
    schema = find_header_row(
        [
            ["раздел"],
            ["Номер аукциона", "Дата окончания приёма заявок", "Объект закупки", "Статус", "Победная ставка", "Кто выиграл", "Поставляемый товар"],
        ]
    )
    assert schema.header_row == 2
    assert schema.purchase_number_column == 1


def test_service_columns_missing() -> None:
    schema = find_header_row(
        [["Номер аукциона", "Дата окончания приёма заявок", "Объект закупки", "Статус", "Победная ставка", "Кто выиграл", "Поставляемый товар"]]
    )
    assert set(SERVICE_COLUMNS).issuperset(schema.missing_service_columns)


def test_skip_non_working_rows() -> None:
    schema = find_header_row(
        [["Номер аукциона", "Дата окончания приёма заявок", "Объект закупки", "Статус", "Победная ставка", "Кто выиграл", "Поставляемый товар"]]
    )
    assert not is_working_row(["", "", ""], schema)
    assert not is_working_row(["Номер аукциона"], schema)
    assert is_working_row(["012345678901"], schema)
    assert is_working_row(["0711200008525000017"], schema)
    assert not is_working_row(["12.01.2026"], schema)
    assert not is_working_row(["Раздел 12.01.2026"], schema)
    assert normalize_purchase_number(123.0) == "123"


def test_purchase_number_validation() -> None:
    assert is_purchase_number("0711200008525000017")
    assert is_purchase_number("012345678901")
    assert not is_purchase_number("12.01.2026")
    assert not is_purchase_number("Номер аукциона")
