from typing import Protocol

from app.domain.models import (
    ContractAmendment,
    ContractResult,
    FinalProtocolResult,
    SpecificationResult,
)


class ProcurementSource(Protocol):
    async def get_final_protocol(self, purchase_number: str) -> FinalProtocolResult | None:
        ...

    async def get_contract(self, purchase_number: str) -> ContractResult | None:
        ...

    async def get_specification(self, purchase_number: str) -> SpecificationResult | None:
        ...

    async def get_amendments(self, purchase_number: str) -> list[ContractAmendment]:
        ...

