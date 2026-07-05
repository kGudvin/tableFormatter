def _digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def validate_inn(value: str | None) -> bool:
    inn = _digits(value)
    if len(inn) == 10:
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        return sum(int(inn[i]) * weights[i] for i in range(9)) % 11 % 10 == int(inn[9])
    if len(inn) == 12:
        weights_11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        weights_12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        check_11 = sum(int(inn[i]) * weights_11[i] for i in range(10)) % 11 % 10
        check_12 = sum(int(inn[i]) * weights_12[i] for i in range(11)) % 11 % 10
        return check_11 == int(inn[10]) and check_12 == int(inn[11])
    return False


def normalize_inn(value: str | None) -> str | None:
    inn = _digits(value)
    return inn if validate_inn(inn) else None

