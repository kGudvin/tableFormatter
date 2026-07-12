from decimal import Decimal

from app.domain.models import (
    ContractResult,
    FinalProtocolResult,
    ProductItem,
    ResultStatus,
    SpecificationResult,
)
from app.services.result_builder import build_official_result


def test_single_supplier_contract_fills_winner_and_price() -> None:
    result = build_official_result(
        "0123",
        None,
        ContractResult("0123", "ООО Ромашка", "7707083893", Decimal("100.00")),
        SpecificationResult("0123", [ProductItem(position=1, manufacturer="A")]),
    )
    assert result.winner_name == "ООО Ромашка"
    assert result.winner_inn == "7707083893"
    assert result.winning_price == Decimal("100.00")


def test_winner_evasion_requires_review() -> None:
    result = build_official_result(
        "0123",
        FinalProtocolResult("0123", "ООО Первый", "7707083893", Decimal("90.00")),
        ContractResult("0123", "ООО Второй", "500100732259", Decimal("95.00")),
        SpecificationResult("0123", [ProductItem(position=1, manufacturer="A")]),
    )
    assert result.initial_winner_name == "ООО Первый"
    assert result.attention_required
    assert result.status == ResultStatus.NEEDS_REVIEW


def test_non_standard_without_contract() -> None:
    result = build_official_result(
        "0123",
        FinalProtocolResult("0123", None, None, None, raw_status="Закупка отменена"),
        None,
        None,
    )
    assert result.status == ResultStatus.NON_STANDARD
    assert result.winner_name == "Закупка отменена"


def test_price_without_winner_requires_review() -> None:
    result = build_official_result(
        "0123",
        FinalProtocolResult("0123", None, None, Decimal("100.00")),
        ContractResult("0123", None, None, Decimal("100.00")),
        None,
    )

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.attention_required
    assert result.review_reason == "Победитель не найден в данных ЕИС"


def test_unresolved_gisp_registry_number_requires_review() -> None:
    result = build_official_result(
        "0123",
        None,
        ContractResult("0123", "ООО Ромашка", "7707083893", Decimal("100.00")),
        SpecificationResult("0123", [ProductItem(position=1, registry_number="10512345")]),
    )

    assert result.status == ResultStatus.NEEDS_REVIEW
    assert result.review_reason == "Производитель не найден в действующем реестре ГИСП: 10512345"
