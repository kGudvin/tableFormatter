from datetime import datetime
from typing import Any, cast

import pytest

from app.db.models import ReviewQueue
from app.review.sync import publish_open_reviews
from app.sheets.fake import FakeSheetsClient

pytestmark = pytest.mark.integration


async def test_publish_open_reviews_appends_missing_rows() -> None:
    item = ReviewQueue(
        purchase_number="012345678901",
        reason="Нужна проверка",
        status="OPEN",
        payload={"price": "100.00"},
    )
    item.created_at = datetime(2026, 1, 2, 3, 4, 5)
    item.updated_at = datetime(2026, 1, 2, 3, 4, 5)
    db = _FakeDb([item])
    sheets = FakeSheetsClient({"'Требуется проверка'!A:F": [["Номер закупки", "Причина"]]})

    count = await publish_open_reviews(cast(Any, db), sheets, "Требуется проверка")

    assert count == 1
    assert sheets.writes == [
        (
            "'Требуется проверка'!A2",
            [
                [
                    "012345678901",
                    "Нужна проверка",
                    "Открыта",
                    '{"price": "100.00"}',
                    "02.01.2026 03:04:05",
                    "02.01.2026 03:04:05",
                ]
            ],
        )
    ]


class _FakeDb:
    def __init__(self, items: list[ReviewQueue]) -> None:
        self.items = items

    async def execute(self, query: Any) -> "_FakeResult":
        return _FakeResult(self.items)


class _FakeResult:
    def __init__(self, items: list[ReviewQueue]) -> None:
        self.items = items

    def scalars(self) -> list[ReviewQueue]:
        return self.items
