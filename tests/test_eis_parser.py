from app.parsers.eis_text import (
    parse_contract_products_html,
    parse_contract_supplier_details,
    parse_contract_text,
    parse_final_protocol_text,
    parse_supplier_results_contract,
)


def test_procurement_card_text_is_not_treated_as_winner() -> None:
    text = (
        "Поставщик: а завершено Объект закупки Персональная электронно-вычислительная "
        "машина в рамках государственного оборонного заказа Заказчик ГЛАВНОЕ УПРАВЛЕНИЕ "
        "Начальная цена 46309600,00"
    )

    assert parse_contract_text(text) == (None, None, "46309600,00")


def test_procurement_card_text_is_not_treated_as_protocol_winner() -> None:
    text = (
        "Победитель: а завершено Объект закупки Персональная электронно-вычислительная "
        "машина Заказчик ГЛАВНОЕ УПРАВЛЕНИЕ Сумма 46309600,00"
    )

    assert parse_final_protocol_text(text) == (None, None, "46309600,00", None)


def test_real_party_name_is_still_extracted() -> None:
    text = 'Поставщик: ООО "Ромашка" ИНН 7707083893 Цена контракта 100,00'

    assert parse_contract_text(text) == ('ООО "Ромашка"', "7707083893", "100,00")


def test_supplier_results_contract_table_is_extracted() -> None:
    html = """
    <tr class="tableBlock__row">
      <td><a href="/epz/contract/contractCard/common-info.html?reestrNumber=1590229122026000015">1590229122026000015</a></td>
      <td>ГЛАВНОЕ УПРАВЛЕНИЕ</td>
      <td>ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ДИОЗОР ЮГ"</td>
      <td>34 037 556,00</td>
      <td>25.05.2026 10:56</td>
    </tr>
    """

    summary = parse_supplier_results_contract(html)

    assert summary is not None
    assert summary.reestr_number == "1590229122026000015"
    assert summary.supplier_name == 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ДИОЗОР ЮГ"'
    assert summary.price == "34 037 556,00"


def test_contract_supplier_details_are_extracted() -> None:
    html = """
    <tr class="tableBlock__row">
      <td>ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ВЕГА" ИНН: 4027147287 КПП: 402701001 Поставщик (подрядчик, исполнитель)</td>
      <td>Расчетный счет в банке</td>
    </tr>
    """

    assert parse_contract_supplier_details(html) == (
        'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ВЕГА"',
        "4027147287",
    )


def test_contract_product_rows_are_extracted() -> None:
    html = """
    <tr class="tableBlock__row">
      <td></td>
      <td>1. Персональные электронно-вычислительные машины. Товарный знак: БЕШТАУ Страна происхождения: Российская Федерация (643)</td>
      <td>Персональные электронно-вычислительные машины (26.20.15.110)</td>
      <td>Товар</td>
      <td>541 ШТ</td>
      <td>62 916,00</td>
      <td>34 037 556,00</td>
    </tr>
    """

    products = parse_contract_products_html(html, "https://zakupki.gov.ru/contract")

    assert len(products) == 1
    assert products[0].country == "Российская Федерация (643)"
    assert products[0].trademark == "БЕШТАУ"
    assert products[0].model == "Персональные электронно-вычислительные машины"
    assert products[0].quantity is not None
    assert str(products[0].quantity) == "541"
    assert products[0].unit == "ШТ"


def test_contract_product_country_stops_before_characteristics() -> None:
    html = """
    <tr class="tableBlock__row">
      <td></td>
      <td>1. Компьютер персональный настольный (моноблок). Товарный знак: Отсутствует Страна происхождения: Российская Федерация (643) Наличие встроенного картридера Да</td>
      <td>Компьютер персональный настольный (моноблок)</td>
      <td>Товар</td>
      <td>7 ШТ</td>
      <td>53 925,28571428571</td>
      <td>377 477,00</td>
    </tr>
    """

    products = parse_contract_products_html(html)

    assert products[0].country == "Российская Федерация (643)"
