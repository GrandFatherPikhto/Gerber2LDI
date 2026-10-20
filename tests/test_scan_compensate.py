"""Spot compensation on a stream of rows (plan Ш1, design §3.4).

Cells closed here:

    круг R после компенсации r      ≈ круг R − r, допуск — шаг строки
    дорожка шириной w               → w − 2r в середине, пусто у торцов
    r = 0                           тождество, ровно те же строки
    r больше полуширины             пусто
    r < шага строки                 окно в одну строку, w − 2r
    пустые строки не поданы         синтезированы — результат совпадает с явными
    строки дальше r от рисунка      пусты
    y мимо сетки сверх допуска      ValueError
    строки не по возрастанию        ValueError
    отрицательный r / нулевой шаг   ValueError
    пустой вход                     пустой выход
    ленивость                       первая строка отдаётся, не прочитав весь слой

Empty cells and why: no cell for a figure lying exactly on the grid edge with
r = 0 — that IS the identity row above; no cell for an infinite input stream's
tail — `--reverse-order` and the laziness cell cover the streaming property, and
an infinite tail cannot be asserted in finite time.
"""
import math

import pytest

from gerber2ldi.scan import ScanRow, compensate

STEP_MM = 0.05


def _disc_rows(radius: float, extent: float, step: float = STEP_MM):
    """Rows of a filled disc of `radius` on a grid from -extent to +extent."""
    count = int(round(2 * extent / step)) + 1
    rows = []
    for i in range(count):
        y = round(-extent + i * step, 10)
        half = math.sqrt(max(0.0, radius * radius - y * y))
        rows.append(ScanRow(y, ((-half, half),) if half > 0.0 else ()))
    return rows


def _strip_rows(half_height: float, half_width: float, extent: float,
                step: float = STEP_MM):
    """Rows of a horizontal strip: ink where |y| <= half_height."""
    count = int(round(2 * extent / step)) + 1
    rows = []
    for i in range(count):
        y = round(-extent + i * step, 10)
        rows.append(ScanRow(y, ((-half_width, half_width),)
                            if abs(y) <= half_height else ()))
    return rows


def test_circle_minus_radius_is_a_smaller_circle():
    """The whole point of the compensation: R becomes R - r, ± one row step.

    A row grid can only take material away (the intersection), never add it, and
    the row step bounds how far it can fall short — so the assertion is a band,
    not an equality.
    """
    radius, spot = 1.0, 0.2
    rows = _disc_rows(radius, extent=1.2)
    out = list(compensate(rows, STEP_MM, spot))

    checked = 0
    for row in out:
        ideal = math.sqrt(max(0.0, (radius - spot) ** 2 - row.y_mm ** 2))
        if ideal <= 2 * STEP_MM:
            continue  # too close to the tip to say anything
        half = (row.segments[0][1] - row.segments[0][0]) / 2.0
        assert abs(half - ideal) <= STEP_MM + 1e-9
        checked += 1
    assert checked > 20, "not enough rows actually compared"


def test_track_narrows_by_both_sides_and_ends_stay_empty():
    """A strip of width w becomes w - 2r in the middle and vanishes near the ends."""
    half_width, half_height, spot = 0.5, 1.0, 0.2
    rows = _strip_rows(half_height, half_width, extent=1.5)
    out = list(compensate(rows, STEP_MM, spot))

    for row in out:
        if abs(row.y_mm) <= half_height - spot - STEP_MM:
            assert row.segments == ((round(-half_width + spot, 10),
                                     round(half_width - spot, 10)),)
        elif abs(row.y_mm) >= half_height - spot + STEP_MM:
            assert row.is_empty


def test_zero_radius_is_the_identity():
    """No spot measured, no compensation — and not a copy that drifts."""
    rows = _strip_rows(1.0, 0.5, extent=1.5)
    out = list(compensate(rows, STEP_MM, 0.0))
    assert out == rows


def test_radius_wider_than_the_track_leaves_nothing():
    """A spot wider than half the track burns it away entirely."""
    rows = _strip_rows(1.0, 0.15, extent=1.2)
    out = list(compensate(rows, STEP_MM, 0.2))
    assert all(row.is_empty for row in out)


def test_radius_below_the_row_step_uses_a_single_row_window():
    """r < step means ±0 rows; the narrowing must still be r on each side."""
    rows = _strip_rows(1.0, 0.5, extent=0.5)
    out = list(compensate(rows, STEP_MM, 0.01))
    for row in out:
        assert row.segments == ((round(-0.49, 10), round(0.49, 10)),)


def test_omitted_empty_rows_equal_explicit_empty_rows():
    """The caller may stream only inked rows: synthesising empties is transparent.

    The figure is two bands with a gap, so the omitted rows are in the MIDDLE and
    the first/last rows of both streams are inked — the two streams then describe
    the same grid range and the results must be identical row for row.
    """
    extent, step = 1.0, STEP_MM
    count = int(round(2 * extent / step)) + 1
    with_empties = []
    for i in range(count):
        y = round(-extent + i * step, 10)
        inked = 0.5 <= abs(y) <= 1.0
        with_empties.append(ScanRow(y, ((-0.5, 0.5),) if inked else ()))
    sparse = [row for row in with_empties if not row.is_empty]

    dense_out = list(compensate(with_empties, step, 0.1))
    sparse_out = list(compensate(sparse, step, 0.1))
    # A synthesised row carries the grid height y0 + i*step, a caller-written one
    # carries its own float — they differ in the last bits, never in the ink.
    assert len(dense_out) == len(sparse_out)
    for dense_row, sparse_row in zip(dense_out, sparse_out):
        assert math.isclose(dense_row.y_mm, sparse_row.y_mm, abs_tol=1e-9)
        assert dense_row.segments == sparse_row.segments


def test_rows_far_from_the_figure_stay_empty():
    """Beyond r there is nothing to intersect with: empty in, empty out."""
    rows = _strip_rows(0.2, 0.5, extent=2.0)
    out = list(compensate(rows, STEP_MM, 0.2))
    assert all(row.is_empty for row in out if abs(row.y_mm) > 0.2 + 0.2)


def test_row_off_the_grid_raises():
    """Two grids mixed together is a caller bug and must be loud."""
    rows = [ScanRow(0.0, ((0.0, 1.0),)), ScanRow(0.0205, ((0.0, 1.0),))]
    with pytest.raises(ValueError, match="not on the grid"):
        list(compensate(rows, 0.02, 0.05))


def test_rounding_noise_on_the_grid_is_accepted():
    """y = y0 + i * step in float lands a hair off; 1e-6 mm of slack absorbs it."""
    rows, y = [], 0.0
    for _ in range(4):
        rows.append(ScanRow(y, ((0.0, 1.0),)))
        y += 0.02  # repeated additions leave float dust in the last bits
    out = list(compensate(rows, 0.02, 0.0))
    assert len(out) == len(rows)


def test_rows_out_of_order_raise():
    """A stream that goes backwards cannot be compensated in one pass."""
    rows = [ScanRow(0.10, ()), ScanRow(0.02, ())]
    with pytest.raises(ValueError, match="increasing"):
        list(compensate(rows, 0.02, 0.05))


@pytest.mark.parametrize("step, radius, message", [
    (0.0, 0.2, "row step"),
    (-0.02, 0.2, "row step"),
    (0.02, -0.1, "spot radius"),
    (float("nan"), 0.2, "row step"),
], ids=["zero step", "negative step", "negative radius", "nan step"])
def test_bad_arguments_raise(step, radius, message):
    """Programmer errors in this layer are ValueError, not a silent default."""
    with pytest.raises(ValueError, match=message):
        list(compensate([ScanRow(0.0, ())], step, radius))


def test_empty_input_gives_empty_output():
    """No rows in, no rows out — not an exception."""
    assert list(compensate([], 0.02, 0.2)) == []


def test_compensation_streams_and_does_not_read_the_whole_layer():
    """First row out after ~window rows read: the layer never sits in memory."""
    read = 0

    def source():
        nonlocal read
        for i in range(1000):
            read += 1
            yield ScanRow(i * 0.02, ((0.0, 1.0),))

    stream = compensate(source(), 0.02, 0.1)  # half window = 5 rows
    first = next(stream)
    assert first.y_mm == 0.0
    assert read <= 2 * 5 + 2
