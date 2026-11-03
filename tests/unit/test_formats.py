import pytest

from app.documents.formats import sheet_format


@pytest.mark.parametrize("w,h,expected", [
    (297, 210, "A4"), (420, 297, "A3"), (297, 420, "A3"), (594, 420, "A2"), (841, 594, "A1"), (1189, 841, "A0"),
    (1189, 420, "A3x4"), (1261, 594, "A2x3"), (630, 297, "A4x3"), (300, 300, "нестандартный 300x300"),
])
def test_sheet_format(w, h, expected):
    assert sheet_format(w, h) == expected
