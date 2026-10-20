"""Calibration REMOVES distortion — cells on the DIRECTION (plan Ш4, design §17).

The mistake this file exists for: the job multiplied coordinates by `scale_x` and
divided by `scale_y`, i.e. applied the machine's error a second time instead of
undoing it. With `scale = 1.0` (what the other tests used) multiplication and
division are the same number, so nothing noticed.

The cell therefore runs a MODEL OF THE MACHINE:

    commanded x, y  →  real:  y_real = y_cmd · scale_y
                             x_real = x_cmd · scale_x + y_real · tg(skew_deg)

(design §17 "Соглашение о знаках": θ > 0 is the real Y axis leaning towards +X)
and requires the drawn figure to land exactly on the figure from the Gerber.

Values: scale_x = 1.002, scale_y = 0.998, θ = 0.1° — each alone and all together,
on both sides. Mutation: drop a correction or flip its sign and these cells go red.
"""
import math

import pytest

from gerber2ldi.job import SIDE_BOTTOM, SIDE_TOP, build_job
from gerber2ldi.raster import row_segments
from gerber2ldi.scan import ScanRow, mirror
from gerber2ldi.source import load_layer

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
FOOTER = "M02*\n"

# Two 30 × 100 mm strips with a gap: asymmetric in X, tall enough that a 0.2 % Y
# error is 0.2 mm — far above the tolerance below.
FIGURE = (HEADER + "%ADD10R,30.000000X100.000000*%\n%LPD*%\nG01*\nD10*\n"
          "X15000000Y50000000D03*\nX75000000Y50000000D03*\n" + FOOTER)

TOLERANCE_MM = 0.002
ROW_STEP_MM = 0.05
EXTENT_TOLERANCE_MM = 0.11
"""Row quantisation is half a step; three halves leave room for rounding but still
see the 0.2 % Y error, which is 0.2 mm over a 100 mm figure."""


def _layer(tmp_path):
    path = tmp_path / "figure.gbr"
    path.write_text(FIGURE)
    return load_layer(path)


def _to_real(x_cmd: float, y_cmd: float, calibration) -> tuple[float, float]:
    """The model of the machine: what it really does with a commanded point."""
    y_real = y_cmd * calibration.scale_y
    x_real = (x_cmd * calibration.scale_x
              + y_real * math.tan(math.radians(calibration.skew_deg)))
    return x_real, y_real


def _drawn_rows(job, calibration) -> tuple[ScanRow, ...]:
    """Every commanded row → the REAL geometry it will draw, shifted to the origin."""
    mapped = []
    for row in job.rows:
        if row.is_empty:
            continue
        segments = tuple(_to_real(start, row.y_mm, calibration)
                         for start, _end in row.segments)
        ends = tuple(_to_real(end, row.y_mm, calibration) for _start, end in row.segments)
        mapped.append(ScanRow(_to_real(0.0, row.y_mm, calibration)[1],
                              tuple(zip((s[0] for s in segments),
                                        (e[0] for e in ends)))))
    shift = min(row.segments[0][0] for row in mapped)
    return tuple(ScanRow(row.y_mm, tuple((start - shift, end - shift)
                                         for start, end in row.segments))
                 for row in mapped)


CALIBRATIONS = [
    ("scale x only", 1.002, 1.0, 0.0),
    ("scale y only", 1.0, 0.998, 0.0),
    ("skew only", 1.0, 1.0, 0.1),
    ("all three", 1.002, 0.998, 0.1),
]


@pytest.mark.parametrize("name, scale_x, scale_y, skew", CALIBRATIONS,
                         ids=[case[0] for case in CALIBRATIONS])
@pytest.mark.parametrize("side", [SIDE_TOP, SIDE_BOTTOM])
def test_drawing_lands_where_the_figure_is(tmp_path, make_machine,
                                           name, scale_x, scale_y, skew, side):
    machine = make_machine(calibration_text=(
        "[calibration]\n"
        f"scale_x = {scale_x}\n"
        f"scale_y = {scale_y}\n"
        f"skew_deg = {skew}\n"
        "measured = 2026-10-20\n"))
    layer = _layer(tmp_path)
    # The layer is its own outline: the cell checks that the calibration removes the
    # distortion, not how two sides are aligned (tests/test_frame.py holds that).
    job = build_job(layer, machine, power=0.5, spot_compensation=False, side=side,
                    frame=layer)
    drawn = _drawn_rows(job, machine.calibration)
    assert drawn, "the job drew nothing"

    # The EXTENT matters as much as the rows: a figure drawn 0.4 % short still has
    # every row in the right place, it just misses the top one.
    extent = max(row.y_mm for row in drawn) - min(row.y_mm for row in drawn)
    figure_height = layer.bbox[3] - layer.bbox[1]
    assert extent == pytest.approx(figure_height, abs=EXTENT_TOLERANCE_MM)

    # What SHOULD be there, in real millimetres, from the figure itself.
    if side == SIDE_BOTTOM:
        center = (layer.bbox[0] + layer.bbox[2]) / 2.0
    else:
        center = None
    expected = []
    for row in drawn:
        intervals = row_segments(layer.shapes, row.y_mm)
        if center is not None:
            intervals = mirror(intervals, center)
        expected.append(intervals)
    shift = min(intervals[0][0] for intervals in expected if intervals)

    compared = 0
    for row, intervals in zip(drawn, expected):
        wanted = tuple((start - shift, end - shift) for start, end in intervals)
        assert len(row.segments) == len(wanted), (row.y_mm, row.segments, wanted)
        for (start, end), (want_start, want_end) in zip(row.segments, wanted):
            assert start == pytest.approx(want_start, abs=TOLERANCE_MM), row.y_mm
            assert end == pytest.approx(want_end, abs=TOLERANCE_MM), row.y_mm
            compared += 1
    assert compared > 100, "not enough segments actually compared"


def test_figure_size_is_preserved_through_the_calibration(tmp_path, make_machine):
    """The direct reading of the bug: a 60 mm gap must stay 60 mm on the real table.

    Multiplying instead of dividing moved it by 0.2 % twice (60.24 mm), which the
    tolerance below (0.002 mm) sees immediately.
    """
    machine = make_machine(calibration_text=(
        "[calibration]\nscale_x = 1.002\nscale_y = 0.998\nskew_deg = 0.1\n"
        "measured = 2026-10-20\n"))
    job = build_job(_layer(tmp_path), machine, power=0.5, spot_compensation=False)
    drawn = _drawn_rows(job, machine.calibration)
    row = max(drawn, key=lambda candidate: len(candidate.segments))
    assert len(row.segments) == 2
    gap = row.segments[1][0] - row.segments[0][1]
    assert gap == pytest.approx(30.0, abs=TOLERANCE_MM)
