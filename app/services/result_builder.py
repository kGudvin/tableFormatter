from app.domain.inn import validate_inn
from app.domain.models import (
    NON_STANDARD_STATUSES,
    ContractResult,
    FinalProtocolResult,
    OfficialResult,
    ResultStatus,
    SpecificationResult,
)


def build_official_result(
    purchase_number: str,
    protocol: FinalProtocolResult | None,
    contract: ContractResult | None,
    specification: SpecificationResult | None,
) -> OfficialResult:
    raw_status = protocol.raw_status if protocol else None
    if raw_status in NON_STANDARD_STATUSES and contract is None:
        return OfficialResult(
            purchase_number=purchase_number,
            winner_name=raw_status,
            winner_inn="-",
            winning_price=None,
            current_contract_price=None,
            products=[],
            status=ResultStatus.NON_STANDARD,
            protocol_url=protocol.protocol_url if protocol else None,
        )

    winner_name = protocol.winner_name if protocol else None
    winner_inn = protocol.winner_inn if protocol else None
    winning_price = protocol.winning_price if protocol else None
    current_contract_price = contract.contract_price if contract else None
    initial_winner_name = None
    attention_required = False
    review_reason = None
    products = specification.products if specification else []

    if contract and contract.supplier_name:
        if winner_name and contract.supplier_name.casefold() != winner_name.casefold():
            initial_winner_name = winner_name
            attention_required = True
            review_reason = "Контракт заключён не с первоначальным победителем"
        winner_name = contract.supplier_name
        winner_inn = contract.supplier_inn
        if winning_price is None:
            winning_price = contract.contract_price

    if not winner_name and any([winning_price, current_contract_price, products]):
        attention_required = True
        review_reason = review_reason or "Победитель не найден в данных ЕИС"
    elif winner_name and (not winner_inn or not validate_inn(winner_inn)):
        attention_required = True
        review_reason = review_reason or "Победитель найден без корректного ИНН"

    if contract and not products:
        attention_required = True
        review_reason = review_reason or "Контракт найден, но спецификация не извлечена"

    unresolved_registry = next(
        (
            item.registry_number
            for item in products
            if item.registry_number and not item.trademark and not item.manufacturer
        ),
        None,
    )
    if unresolved_registry:
        attention_required = True
        review_reason = review_reason or (
            f"Производитель не найден в действующем реестре ГИСП: {unresolved_registry}"
        )

    status = ResultStatus.NEEDS_REVIEW if attention_required else ResultStatus.CONFIRMED
    if not any([winner_name, winner_inn, winning_price, current_contract_price, products]):
        status = ResultStatus.NOT_FOUND_YET

    return OfficialResult(
        purchase_number=purchase_number,
        winner_name=winner_name,
        winner_inn=winner_inn,
        winning_price=winning_price,
        current_contract_price=current_contract_price,
        products=products,
        status=status,
        protocol_url=protocol.protocol_url if protocol else None,
        contract_url=contract.contract_url if contract else None,
        specification_url=specification.specification_url if specification else None,
        initial_winner_name=initial_winner_name,
        attention_required=attention_required,
        review_reason=review_reason,
    )
