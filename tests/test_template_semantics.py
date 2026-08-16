from __future__ import annotations

import pytest

from archaeoforge.template_semantics import meets_minimum_recognizability, template_recognizability


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("sphinx", "identity_specific"),
        ("SPHINX", "identity_specific"),
        ("pyramid", "type_specific"),
        ("gate", "type_specific"),
        ("building", "generic_envelope"),
        ("temple", "generic_envelope"),
        ("future_unknown_template", "generic_envelope"),
        (None, "generic_envelope"),
    ],
)
def test_template_recognizability_is_conservative(template, expected):
    assert template_recognizability(template) == expected


def test_minimum_recognizability_orders_generic_type_and_identity_templates():
    assert meets_minimum_recognizability("sphinx", "identity_specific") is True
    assert meets_minimum_recognizability("sphinx", "type_specific") is True
    assert meets_minimum_recognizability("pyramid", "type_specific") is True
    assert meets_minimum_recognizability("pyramid", "identity_specific") is False
    assert meets_minimum_recognizability("building", "type_specific") is False
