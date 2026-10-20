"""The preview PNG: drawn from the ROWS, top of the file = largest y (plan Ш4).

Cells:

    ориентация        несимметричная фигура: верх картинки там, где большой y
    размер            длинная сторона ровно long_side_px
    отказы            пустое поле, неположительная длинная сторона

Пустых клеток нет: цвет (медь — белая) проверяется тем же тестом ориентации.
"""
import numpy as np
import pytest
from PIL import Image

from gerber2ldi.preview import MAX_PREVIEW_PX, preview_step_mm, write_preview
from gerber2ldi.scan import ScanRow

BOX = (0.0, 0.0, 10.0, 10.0)


def _asymmetric_rows() -> list[ScanRow]:
    """Low band on the right half, high band on the left half.

    A full-width band looks the same upside down — that is exactly how the mirrored
    preview of step 0 survived being counted.
    """
    rows = []
    for step in range(10):  # y = 0.05 … 0.95, right half
        rows.append(ScanRow(0.05 + step * 0.1, ((5.0, 10.0),)))
    for step in range(10):  # y = 9.05 … 9.95, left half
        rows.append(ScanRow(9.05 + step * 0.1, ((0.0, 5.0),)))
    return rows


def test_preview_top_is_the_largest_y(tmp_path):
    target = write_preview(tmp_path / "preview.png", _asymmetric_rows(), BOX,
                           long_side_px=100)
    picture = np.asarray(Image.open(target).convert("L")) > 127
    assert picture.shape == (100, 100)
    assert picture[:10, :50].mean() > 0.9, "high y on the left must be TOP-LEFT"
    assert picture[:10, 50:].mean() < 0.1
    assert picture[-10:, 50:].mean() > 0.9, "low y on the right must be BOTTOM-RIGHT"
    assert picture[-10:, :50].mean() < 0.1
    assert picture[40:60, :].mean() < 0.1, "the middle must be empty"


def test_preview_long_side_matches_the_request(tmp_path):
    target = write_preview(tmp_path / "preview.png", _asymmetric_rows(),
                           (0.0, 0.0, 10.0, 20.0), long_side_px=200)
    width, height = Image.open(target).size
    assert max(width, height) == 200
    assert height > width, "a 10 × 20 mm field is taller than wide"


def test_default_long_side_is_the_module_constant():
    assert preview_step_mm(BOX) == pytest.approx(10.0 / MAX_PREVIEW_PX)


@pytest.mark.parametrize("box, long_side, message", [
    (BOX, 0, "long side"),
    ((0.0, 0.0, 0.0, 0.0), 100, "empty"),
], ids=["zero long side", "empty field"])
def test_preview_refuses_impossible_sizes(box, long_side, message):
    with pytest.raises(ValueError, match=message):
        preview_step_mm(box, long_side)
