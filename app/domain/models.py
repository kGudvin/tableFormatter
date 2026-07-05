from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ProcessingState(StrEnum):
    NEW = "NEW"
    WAITING_FOR_FINAL_PROTOCOL = "WAITING_FOR_FINAL_PROTOCOL"
    FINAL_PROTOCOL_FOUND = "FINAL_PROTOCOL_FOUND"
    WAITING_FOR_CONTRACT = "WAITING_FOR_CONTRACT"
    CONTRACT_FOUND = "CONTRACT_FOUND"
    WAITING_FOR_SPECIFICATION = "WAITING_FOR_SPECIFICATION"
    SPECIFICATION_FOUND = "SPECIFICATION_FOUND"
    MONITORING_AMENDMENTS = "MONITORING_AMENDMENTS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class ResultStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    NON_STANDARD = "NON_STANDARD"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_FOUND_YET = "NOT_FOUND_YET"


NON_STANDARD_STATUSES = {
    "Закупка отменена",
    "Закупка не состоялась",
    "Не подано ни одной заявки",
    "Все заявки отклонены",
    "Подана одна заявка",
    "Контракт не заключён",
    "Победитель уклонился",
    "Результаты аннулированы",
}


@dataclass(frozen=True)
class ProductItem:
    position: int
    country: str | None = None
    manufacturer: str | None = None
    trademark: str | None = None
    model: str | None = None
    registry_number: str | None = None
    registry_url: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    source_url: str | None = None
    specification_version: str | None = None


@dataclass(frozen=True)
class FinalProtocolResult:
    purchase_number: str
    winner_name: str | None
    winner_inn: str | None
    winning_price: Decimal | None
    status: ResultStatus = ResultStatus.NOT_FOUND_YET
    protocol_url: str | None = None
    published_at: datetime | None = None
    raw_status: str | None = None


@dataclass(frozen=True)
class ContractResult:
    purchase_number: str
    supplier_name: str | None
    supplier_inn: str | None
    contract_price: Decimal | None
    contract_url: str | None = None
    signed_at: date | None = None
    initial_winner_name: str | None = None


@dataclass(frozen=True)
class SpecificationResult:
    purchase_number: str
    products: list[ProductItem] = field(default_factory=list)
    specification_url: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class ContractAmendment:
    purchase_number: str
    amendment_url: str
    published_at: datetime | None
    specification_changed: bool = False
    contract_price: Decimal | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OfficialResult:
    purchase_number: str
    winner_name: str | None
    winner_inn: str | None
    winning_price: Decimal | None
    current_contract_price: Decimal | None
    products: list[ProductItem]
    status: ResultStatus
    protocol_url: str | None = None
    contract_url: str | None = None
    specification_url: str | None = None
    initial_winner_name: str | None = None
    attention_required: bool = False
    review_reason: str | None = None

