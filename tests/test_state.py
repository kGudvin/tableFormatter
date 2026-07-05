import pytest

from app.domain.models import ProcessingState
from app.domain.state import transition


def test_allowed_transition() -> None:
    assert (
        transition(ProcessingState.NEW, ProcessingState.WAITING_FOR_FINAL_PROTOCOL)
        == ProcessingState.WAITING_FOR_FINAL_PROTOCOL
    )


def test_forbidden_transition() -> None:
    with pytest.raises(ValueError):
        transition(ProcessingState.COMPLETED, ProcessingState.NEW)

