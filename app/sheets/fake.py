from typing import Any


class FakeSheetsClient:
    def __init__(self, ranges: dict[str, list[list[Any]]] | None = None) -> None:
        self.ranges = ranges or {}
        self.writes: list[tuple[str, list[list[Any]]]] = []
        self.requests: list[dict[str, Any]] = []

    async def read_values(self, range_name: str) -> list[list[Any]]:
        return self.ranges.get(range_name, [])

    async def write_values(self, range_name: str, values: list[list[Any]]) -> None:
        self.writes.append((range_name, values))
        self.ranges[range_name] = values

    async def batch_update(self, requests: list[dict[str, Any]]) -> None:
        self.requests.extend(requests)

