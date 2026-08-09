from __future__ import annotations

import pytest

from archaeoforge.compile_scene import _as_list
from archaeoforge.validate import _water_solid_overlap_issue


def test_malformed_json_list_is_not_silently_treated_as_delimited_text() -> None:
    with pytest.raises(ValueError, match="Malformed JSON list"):
        _as_list('["EVID-1", broken]')


def test_overlap_check_failure_becomes_a_visible_validation_issue() -> None:
    class BrokenGeometry:
        def intersection(self, other):
            raise RuntimeError("topology failure")

    issue = _water_solid_overlap_issue("RIVER", BrokenGeometry(), "PALACE", object())

    assert issue is not None
    assert issue.code == "WATER_SOLID_OVERLAP_CHECK_FAILED"
    assert issue.severity == "warning"
    assert "topology failure" in issue.message
    assert "not checked" in issue.remediation
