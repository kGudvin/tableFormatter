from app.domain.snapshots import field_hash, is_manual_change, normalize_field_value


def test_normalize_field_value() -> None:
    assert normalize_field_value("  ООО   Ромашка\n") == "ооо ромашка"


def test_manual_change_detection() -> None:
    assert not is_manual_change("ООО Ромашка", " ооо   ромашка ")
    assert is_manual_change("ООО Василек", "ООО Ромашка")
    assert field_hash("A") == field_hash(" a ")

