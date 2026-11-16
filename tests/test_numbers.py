import pytest

from esg_lens.llm.base import parse_json_object
from esg_lens.numbers import digits_signature, parse_value


@pytest.mark.parametrize(
    "text, value, unit",
    [
        ("48,250 tCO2e", 48250, "tCO2e"),
        ("1.2 million tCO2e", 1_200_000, "tCO2e"),
        ("410 GWh", 410, "GWh"),
        ("64%", 64, "%"),
        ("12 900 t CO2e", 12900, "tCO2e"),
        ("3,5 MWh", 3.5, "MWh"),
        (None, None, None),
        ("no figure here", None, None),
    ],
)
def test_parse_value(text, value, unit):
    assert parse_value(text) == (value, unit)


@pytest.mark.parametrize("value, sig", [(1_200_000, "12"), (48250, "4825"), (64, "64"), (12.5, "125")])
def test_digits_signature(value, sig):
    assert digits_signature(value) == sig


def test_parse_json_object_handles_fences_and_prose():
    assert parse_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('result: {"b": [1, 2]} done') == {"b": [1, 2]}
    with pytest.raises(ValueError):
        parse_json_object("no json")
