from app.domain.inn import normalize_inn, validate_inn


def test_validate_legal_entity_inn() -> None:
    assert validate_inn("7707083893")
    assert normalize_inn(" 770 708 3893 ") == "7707083893"


def test_validate_person_inn() -> None:
    assert validate_inn("500100732259")


def test_reject_invalid_inn() -> None:
    assert not validate_inn("7707083894")
    assert not validate_inn("123")

