"""Rasterisation by geometry: rows, not an image (plan Ш2).

Cells closed here:

    y строки = центр проезда       y0 + (i + 0.5)·step, один хозяин формулы
    прямоугольник 10×2 мм           нужное число строк, отрезок (0, 10) в каждой
    светлая полярность вырезает     дырка в регионе даёт ДВА отрезка в строке
    регион G36/G37                  заливка читается как площадь, не как контур
    порядок фигур                   LPC применяется к тому, что нарисовано раньше
    ориентация TIFF                 верх картинки — это max y (переворот при записи)
    отказы                          нулевой шаг строк

Пустые клетки: изображение в полном разрешении не пишется в тесте (200 МБ на
поле 100 мм при 2.5 мкм) — превью проверяется на сетке 0.1 мм, а потоковость
строки держится тем, что `iter_rows` отдаёт генератор и `ScanRow` создаётся по
одному; замер памяти — в отчёте Ш0.
"""
import numpy as np
import pytest

from gerber2ldi.raster import (iter_rows, row_count, row_segments, row_y,
                               rows_to_image, write_tiff)
from gerber2ldi.scan import ScanRow
from gerber2ldi.source import Shape



def test_row_height_is_the_centre_of_the_pass():
    """y = y0 + (i + 0.5)·step — the one rule the preview and the templates share."""
    assert row_y(0.0, 0, 0.02) == pytest.approx(0.01)
    assert row_y(0.0, 9, 0.02) == pytest.approx(0.19)
    assert row_y(-5.0, 2, 0.02) == pytest.approx(-5.0 + 2.5 * 0.02)


def test_row_count_rounds_and_refuses_zero_step():
    assert row_count(2.0, 0.02) == 100
    assert row_count(0.0, 0.02) == 0
    with pytest.raises(ValueError, match="row step"):
        row_count(2.0, 0.0)


def _rect_shape(x0, y0, x1, y1, dark=True) -> Shape:
    polygon = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return Shape(polygons=(polygon,), dark=dark, bbox=(x0, y0, x1, y1))


def test_rectangle_row_is_one_segment():
    """A 10×2 mm rectangle: every row carries exactly (0, 10)."""
    shape = _rect_shape(0.0, 0.0, 10.0, 2.0)
    rows = list(iter_rows([shape], shape.bbox, 0.02))
    assert len(rows) == 100
    for row in rows:
        assert row.segments == ((0.0, 10.0),)


def test_clear_object_cuts_two_segments():
    """A dark region with a clear hole in it: the row through the hole has two parts."""
    dark = _rect_shape(0.0, 0.0, 10.0, 10.0)
    clear = _rect_shape(3.0, 4.0, 7.0, 6.0, dark=False)
    row = row_segments([dark, clear], 5.0)
    assert row == ((0.0, 3.0), (7.0, 10.0))
    # Outside the hole the row is whole again.
    assert row_segments([dark, clear], 1.0) == ((0.0, 10.0),)


def test_polarity_follows_file_order():
    """Draw, erase, draw again: the second dark object covers part of the hole."""
    dark = _rect_shape(0.0, 0.0, 10.0, 10.0)
    clear = _rect_shape(3.0, 4.0, 7.0, 6.0, dark=False)
    again = _rect_shape(5.0, 4.0, 7.0, 6.0, dark=True)
    assert row_segments([dark, clear, again], 5.0) == ((0.0, 3.0), (5.0, 10.0))


def test_clear_before_dark_changes_nothing_but_is_not_reordered():
    """A clear object first erases an empty row; the later dark one still draws."""
    clear = _rect_shape(3.0, 4.0, 7.0, 6.0, dark=False)
    dark = _rect_shape(0.0, 0.0, 10.0, 10.0)
    assert row_segments([clear, dark], 5.0) == ((0.0, 10.0),)


def test_iter_rows_yields_scan_rows_with_centres():
    shape = _rect_shape(0.0, 0.0, 1.0, 0.1)
    rows = list(iter_rows([shape], (0.0, 0.0, 1.0, 0.1), 0.02))
    assert all(isinstance(row, ScanRow) for row in rows)
    assert [round(row.y_mm, 6) for row in rows] == [0.01, 0.03, 0.05, 0.07, 0.09]


def test_tiff_is_written_top_down(tmp_path):
    """Rows ascend in y, the FILE descends: what sits at high y must land on TOP.

    The figure is deliberately ASYMMETRIC — high band on the left, low band on the
    right. Full-width bands look identical upside down, which is exactly why the
    mirrored preview from step 0 survived being counted and eyeballed.
    """
    high_left = _rect_shape(0.0, 9.0, 5.0, 10.0)
    low_right = _rect_shape(5.0, 0.0, 10.0, 1.0)
    box = (0.0, 0.0, 10.0, 10.0)
    rows = list(iter_rows([high_left, low_right], box, 0.1))
    target = write_tiff(tmp_path / "preview.tif", rows, box, 0.1)

    from PIL import Image
    picture = np.asarray(Image.open(target).convert("L")) > 127
    assert picture.shape == (100, 100)
    assert picture[:10, :50].mean() > 0.9, "y ≈ 9.5 on the left must be TOP-LEFT"
    assert picture[:10, 50:].mean() < 0.1
    assert picture[-10:, 50:].mean() > 0.9, "y ≈ 0.5 on the right must be BOTTOM-RIGHT"
    assert picture[-10:, :50].mean() < 0.1
    assert picture[40:60, :].mean() < 0.1, "the middle must be empty"


def test_rows_to_image_refuses_a_field_thinner_than_a_pixel():
    with pytest.raises(ValueError, match="narrower"):
        rows_to_image([], (0.0, 0.0, 0.001, 1.0), 0.01)
    with pytest.raises(ValueError, match="X step"):
        rows_to_image([], (0.0, 0.0, 1.0, 1.0), 0.0)
