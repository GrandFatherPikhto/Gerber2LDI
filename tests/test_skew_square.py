"""The skew square measures the machine, not itself (plan Ш5, design §17).

Two things are being checked, and they are different:

  * DIRECTION — a square is drawn by a MODEL OF THE MACHINE that bends the axes by
    theta, the pattern goes through `build_job`, and the diagonals read back from the
    DRAWN rows must give theta with the right sign and within 0.005 deg;
  * DISCIPLINE — without `--with-calibration` the square must ignore the profile's
    scale and skew (it measures them, so drawing it through them would measure their
    residual), and WITH the flag at a calibration equal to the real distortion the
    measurement must be a residual of about zero.

Sign convention (design §17, the calibration comments in the profile):

    x_real = x_commanded + y_real · tg(theta),  theta > 0 = the real Y axis leans +X
    d1 = front-left -> back-right   (the LONGER one when theta > 0)
    d2 = front-right -> back-left
    theta = (d1 - d2) / (sqrt(2) · L), L measured with calipers

The swapped-diagonals control is here because a sign error turns a correction into a
DOUBLING of the error between the two sides of a double-sided board.
"""
import math

import pytest

from gerber2ldi.errors import JobError
from gerber2ldi.job import build_job
from gerber2ldi.patterns import (diagonals_from_rows, skew_from_diagonals, square)
from gerber2ldi.scan import ScanRow

SIDE_MM = 150.0
TOLERANCE_DEG = 0.005
"""The user's acceptance cell: +0.1 deg must come back as +0.1 ± 0.005."""

REAL = ("[calibration]\n"
        "scale_x = 1.002\n"
        "scale_y = 0.998\n"
        "skew_deg = 0.1\n"
        "spot_radius_um = 150\n"
        "measured = 2026-10-20\n")


def _to_real(x_cmd: float, y_cmd: float, calibration) -> tuple[float, float]:
    """The model of the machine: what it really does with a commanded point."""
    y_real = y_cmd * calibration.scale_y
    x_real = (x_cmd * calibration.scale_x
              + y_real * math.tan(math.radians(calibration.skew_deg)))
    return x_real, y_real


def _drawn_rows(job, calibration) -> tuple[ScanRow, ...]:
    """Every commanded row → the REAL geometry the machine will draw."""
    drawn = []
    for row in job.rows:
        if row.is_empty:
            continue
        starts = tuple(_to_real(start, row.y_mm, calibration)[0] for start, _ in row.segments)
        ends = tuple(_to_real(end, row.y_mm, calibration)[0] for _, end in row.segments)
        drawn.append(ScanRow(_to_real(0.0, row.y_mm, calibration)[1],
                             tuple(zip(starts, ends))))
    return tuple(drawn)


def _measured_deg(pattern, real, *, side_mm: float = SIDE_MM) -> float:
    job = build_job(pattern.layer, pattern.machine, power=0.6,
                    notes=pattern.notes, spot_compensation=False)
    d1, d2 = diagonals_from_rows(_drawn_rows(job, real))
    return skew_from_diagonals(d1, d2, side_mm)


def test_the_square_ignores_the_calibration_it_measures(make_machine):
    """Absolute measurement: the profile says 0.1 deg, so the square must read 0.1.

    The profile here IS the distortion model, so a square drawn through it would read
    a residual of ~0 — the mistake this cell exists for.
    """
    machine = make_machine(calibration_text=REAL)
    pattern = square(machine)
    assert pattern.machine.calibration.scale_x == pytest.approx(1.0)
    assert pattern.machine.calibration.scale_y == pytest.approx(1.0)
    assert pattern.machine.calibration.skew_deg == pytest.approx(0.0)
    assert pattern.machine.calibration.spot_radius_mm == pytest.approx(0.15), \
        "the measured spot is KEPT, so --with-compensation still has a number"
    assert "calibration: NOT applied" in pattern.notes[-1]

    measured = _measured_deg(pattern, machine.calibration)
    assert measured == pytest.approx(0.1, abs=TOLERANCE_DEG)

    # Control: swapping the diagonals flips the sign — two orders outside the tolerance.
    job = build_job(pattern.layer, pattern.machine, power=0.6,
                    spot_compensation=False)
    d1, d2 = diagonals_from_rows(_drawn_rows(job, machine.calibration))
    swapped = skew_from_diagonals(d2, d1, SIDE_MM)
    assert swapped == pytest.approx(-measured, abs=1e-9), "the sign must flip"
    assert abs(swapped - measured) > 20 * TOLERANCE_DEG


def test_with_the_flag_the_measurement_is_a_residual_of_zero(make_machine):
    """Calibration equal to the real distortion: what is left to correct is ~nothing."""
    machine = make_machine(calibration_text=REAL)
    pattern = square(machine, with_calibration=True)
    assert pattern.machine.calibration.skew_deg == pytest.approx(0.1)
    assert "calibration: applied" in pattern.notes[-1]
    measured = _measured_deg(pattern, machine.calibration)
    assert measured == pytest.approx(0.0, abs=TOLERANCE_DEG)


def test_the_sign_follows_the_diagonal_that_is_longer(make_machine):
    """theta < 0 is the real Y axis leaning towards −X: d1 becomes the shorter one."""
    machine = make_machine(calibration_text=REAL.replace("skew_deg = 0.1",
                                                         "skew_deg = -0.1"))
    measured = _measured_deg(square(machine), machine.calibration)
    assert measured == pytest.approx(-0.1, abs=TOLERANCE_DEG)
    assert measured < 0.0


def test_the_formula_matches_the_design_number():
    """Design §17: at L = 150 mm a 0.37 mm diagonal difference is 0.1 deg."""
    assert skew_from_diagonals(200.0 + 0.370, 200.0, 150.0) == pytest.approx(
        0.1, abs=0.0005)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan")])
def test_the_formula_refuses_nonsense(bad):
    with pytest.raises(ValueError):
        skew_from_diagonals(200.0, 200.0, bad)


def test_square_geometry_is_a_contour_not_a_filled_square(make_machine):
    pattern = square(make_machine(calibration_text=REAL), side_mm=100.0,
                     line_width_mm=0.2)
    shapes = pattern.layer.shapes
    assert len(shapes) == 4, "front, back, left, right"
    assert pattern.layer.bbox == pytest.approx((0.0, 0.0, 100.0, 100.0))
    for shape in shapes:
        width = shape.bbox[2] - shape.bbox[0]
        height = shape.bbox[3] - shape.bbox[1]
        assert min(width, height) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        square(make_machine(calibration_text=REAL), side_mm=0.3, line_width_mm=0.2)


def test_a_square_too_big_for_the_table_is_refused_with_numbers(make_machine):
    machine = make_machine(calibration_text=REAL)
    pattern = square(machine, side_mm=205.0)  # 205 + 2·5 mm of run-up on a 210 table
    with pytest.raises(JobError, match="across"):
        build_job(pattern.layer, pattern.machine, power=0.6, spot_compensation=False)
    fits = square(machine, side_mm=SIDE_MM)
    job = build_job(fits.layer, fits.machine, power=0.6, spot_compensation=False)
    assert job.box[2] - job.box[0] == pytest.approx(SIDE_MM, abs=0.01)
