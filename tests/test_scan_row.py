"""Rows and the single normalisation (plan Ш1, "Общая середина").

Cells closed here — the table of the one function that transforms values:

    вход                  ожидание
    пустая                пустая
    один отрезок          тот же
    касающиеся            слиты в один
    перекрытые            слиты в один
    вложенные             внешний
    дубликат              один
    неотсортированные     по x0
    нулевая длина         выброшена
    нулевая длина внутри  выброшена, соседи не слиты через неё
    обратный отрезок      ValueError, НЕ выброс

Empty cells and why: no cell for NaN/inf coordinates — gerbonara never produces
them and the input layer (Ш2) rejects bad geometry before it gets here; no cell for
-0.0 (it equals 0.0 and is a zero-length segment, covered).
"""
import math

import pytest

from gerber2ldi.scan import ScanRow, normalize

NORMALIZE_CELLS = [
    ("empty", (), ()),
    ("one", ((1.0, 2.0),), ((1.0, 2.0),)),
    ("touching", ((0.0, 1.0), (1.0, 2.0)), ((0.0, 2.0),)),
    ("overlapping", ((0.0, 1.5), (1.0, 2.0)), ((0.0, 2.0),)),
    ("nested", ((0.0, 3.0), (1.0, 2.0)), ((0.0, 3.0),)),
    ("nested reverse order", ((1.0, 2.0), (0.0, 3.0)), ((0.0, 3.0),)),
    ("duplicate", ((1.0, 2.0), (1.0, 2.0)), ((1.0, 2.0),)),
    ("unsorted", ((5.0, 6.0), (1.0, 2.0)), ((1.0, 2.0), (5.0, 6.0))),
    ("zero length", ((1.0, 1.0),), ()),
    ("zero length inside", ((0.0, 1.0), (1.0, 1.0), (2.0, 3.0)),
     ((0.0, 1.0), (2.0, 3.0))),
    ("chain", ((0.0, 1.0), (2.0, 3.0), (1.0, 2.0)), ((0.0, 3.0),)),
]


@pytest.mark.parametrize("name, given, expected",
                         NORMALIZE_CELLS, ids=[c[0] for c in NORMALIZE_CELLS])
def test_normalize_table(name, given, expected):
    """normalize() sorts, merges what touches or overlaps, drops zero length."""
    assert normalize(given) == expected


def test_normalize_reversed_segment_raises():
    """A reversed segment is a broken transformation, not data to drop."""
    with pytest.raises(ValueError, match="reversed segment"):
        normalize(((2.0, 1.0),))


def test_normalize_is_idempotent():
    """Applying it to its own output changes nothing — every op relies on this."""
    once = normalize(((5.0, 6.0), (0.0, 1.0), (0.5, 1.5), (3.0, 3.0)))
    assert normalize(once) == once


def test_scan_row_normalises_on_construction():
    """The invariant is enforced once, at the type, not by every consumer."""
    row = ScanRow(12.5, ((2.0, 3.0), (0.0, 1.0), (1.0, 2.0)))
    assert row.segments == ((0.0, 3.0),)
    assert row.y_mm == 12.5


def test_scan_row_is_immutable():
    """frozen: no consumer can rewrite a row that another layer is streaming."""
    row = ScanRow(0.0, ((0.0, 1.0),))
    with pytest.raises(Exception):
        row.segments = ()  # type: ignore[misc]


@pytest.mark.parametrize("y", [math.nan, math.inf, -math.inf])
def test_scan_row_rejects_non_finite_height(y):
    """A row height comes from the raster grid; NaN/inf means the grid is broken."""
    with pytest.raises(ValueError, match="finite"):
        ScanRow(y, ())


def test_scan_row_empty_helper():
    """Rows outside the figure are the empty form, not a special case."""
    row = ScanRow.empty(3.5)
    assert row.is_empty
    assert row.segments == ()
    assert not ScanRow(3.5, ((0.0, 1.0),)).is_empty
