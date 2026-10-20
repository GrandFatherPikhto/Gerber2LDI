# gerber2ldi/patterns/square.py
"""The skew square: an L×L contour, its diagonals, and the formulas that follow.

Draw the square, then measure with calipers both diagonals AND both sides — the
diagonals carry the skew, the sides carry the scale:

    theta = (d1 - d2) / (sqrt(2) * L)      [radians]

with L MEASURED, not nominal: with an uncalibrated Y scale the drawn square is not
the nominal size.

SIGN CONVENTION (design §17 and the calibration comments in the profile): theta > 0
means the real Y axis leans towards +X, i.e. ``x_real = x_commanded + y·tg(theta)``.
Then the corner at (0, L) is pushed to +x and the diagonal from the FRONT-LEFT
corner (0, 0) to the BACK-RIGHT corner (L, L) becomes the LONGER one:

    d1 = front-left  -> back-right
    d2 = front-right -> back-left

Swapping them flips the sign of the answer — that is the must-fail control of the
test, because a sign error here DOUBLES the error between the two sides of a
double-sided board instead of removing it (the finding of the Ш4 acceptance).

HOW A MEASUREMENT BECOMES A CALIBRATION — the two quantities combine DIFFERENTLY
(Денис, приёмка Ш5): a scale is a FACTOR, so the new value is the old one
MULTIPLIED by ``measured side / commanded side``; a skew is an ANGLE, so the residual
theta is ADDED. `update_formulas()` prints both with the machine's numbers already in
place, because "add the residual" is wrong for the scales and "multiply" is nonsense
for the angle.

CALIBRATION IS NOT APPLIED by default: the square measures scale_x, scale_y and
skew, so drawing it through them would measure their residual. `without_calibration`
keeps the measured spot radius, so `--with-compensation` still has a number to work
with. `--with-calibration` keeps the profile's values, and then the printed formulas
already carry the old factor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

from ..job import Machine
from ..scan import ScanRow
from .base import Pattern, calibration_note
from .shapes import layer_of, rectangle

DEFAULT_SIDE_MM = 150.0
DEFAULT_LINE_WIDTH_MM = 0.2


@dataclass(frozen=True, slots=True)
class SquareMeasurement:
    """What the DRAWN rows say: both diagonals and both sides, in millimetres."""

    d1_mm: float
    d2_mm: float
    x_side_mm: float
    y_side_mm: float


def without_calibration(machine: Machine) -> Machine:
    """The same machine with the measured scale and skew removed, the spot kept."""
    neutral = replace(machine.calibration, scale_x=1.0, scale_y=1.0, skew_deg=0.0)
    return replace(machine, calibration=neutral)


def update_formulas(machine: Machine, *, side_mm: float,
                    with_calibration: bool) -> tuple[str, ...]:
    """The calibration update as FORMULAS with the numbers in place (ASCII).

    Scale is a factor (new = old × measured/commanded); skew is an angle (new = old +
    residual). With ``with_calibration=False`` the square was drawn WITHOUT the
    profile's calibration, so the measurement is absolute and there is no old factor.
    """
    calibration = machine.calibration
    nominal = f"{side_mm:.3f} mm"
    if with_calibration:
        return (
            f"scale_x_new = {calibration.scale_x:.6f} * (measured X side / {nominal})",
            f"scale_y_new = {calibration.scale_y:.6f} * (measured Y side / {nominal})",
            f"skew_new = {calibration.skew_deg:.4f} deg + theta  "
            f"(theta = (d1 - d2) / (sqrt(2) * measured side))",
        )
    return (
        f"scale_x = measured X side / {nominal}",
        f"scale_y = measured Y side / {nominal}",
        "skew_deg = theta  (theta = (d1 - d2) / (sqrt(2) * measured side), "
        "d1 front-left to back-right)",
    )


def square(machine: Machine, *, side_mm: float = DEFAULT_SIDE_MM,
           line_width_mm: float = DEFAULT_LINE_WIDTH_MM,
           with_calibration: bool = False) -> Pattern:
    """A square contour at the origin; the machine model follows `with_calibration`."""
    if not side_mm > 0.0:
        raise ValueError(f"the square side must be positive, got {side_mm!r}")
    if not line_width_mm > 0.0:
        raise ValueError(f"the line width must be positive, got {line_width_mm!r}")
    if side_mm <= 2.0 * line_width_mm:
        raise ValueError(
            f"the square side {side_mm!r} mm must exceed two line widths "
            f"({2.0 * line_width_mm!r} mm), or the contour is solid")

    shapes = (
        rectangle(0.0, 0.0, side_mm, line_width_mm),                 # front
        rectangle(0.0, side_mm - line_width_mm, side_mm, side_mm),   # back
        rectangle(0.0, 0.0, line_width_mm, side_mm),                 # left
        rectangle(side_mm - line_width_mm, 0.0, side_mm, side_mm),   # right
    )
    drawn_with = machine if with_calibration else without_calibration(machine)
    formulas = update_formulas(machine, side_mm=side_mm,
                               with_calibration=with_calibration)
    notes = (
        f"pattern: skew square, side {side_mm:.3f} mm, line {line_width_mm:.3f} mm",
        "measure the diagonals: d1 front-left to back-right, d2 front-right to back-left",
        # The FILE's values, even when they were stripped for the drawing: the note
        # says what the PROFILE has, not what the square was drawn with.
        calibration_note(machine, applied=with_calibration),
        *formulas,
    )
    return Pattern(layer=layer_of(shapes), machine=drawn_with, notes=notes,
                   formulas=formulas)


def skew_from_diagonals(d1_mm: float, d2_mm: float, side_mm: float) -> float:
    """Skew in DEGREES, with the sign of the convention in the module docstring."""
    for name, value in (("d1", d1_mm), ("d2", d2_mm), ("side", side_mm)):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return math.degrees((d1_mm - d2_mm) / (math.sqrt(2.0) * side_mm))


def measure_from_rows(rows: Iterable[ScanRow]) -> SquareMeasurement:
    """Diagonals and sides of the DRAWN contour, from its outermost rows.

    The front bar spans the full width, so its row carries both front corners and the
    X side exactly: the row shear shifts both ends together and cancels. The Y side is
    the extent of the outermost row centres plus one row step — every row paints half
    a step above and below its centre, so that is the drawn edge. The operator
    measures with calipers anyway; these numbers are for the tests and the report.
    """
    inked = [row for row in rows if row.segments]
    if len(inked) < 2:
        raise ValueError(f"need at least two inked rows, got {len(inked)}")
    front, back = inked[0], inked[-1]
    front_left, front_right = front.segments[0][0], front.segments[-1][1]
    back_left, back_right = back.segments[0][0], back.segments[-1][1]
    height = back.y_mm - front.y_mm
    step = min(later.y_mm - earlier.y_mm
               for earlier, later in zip(inked, inked[1:]))
    if not step > 0.0:
        raise ValueError(f"rows must ascend in y, got a step of {step!r}")
    return SquareMeasurement(
        d1_mm=math.hypot(back_right - front_left, height),
        d2_mm=math.hypot(front_right - back_left, height),
        x_side_mm=front_right - front_left,
        y_side_mm=height + step,
    )


def diagonals_from_rows(rows: Iterable[ScanRow]) -> tuple[float, float]:
    """(d1, d2) — a shortcut for :func:`measure_from_rows`."""
    measurement = measure_from_rows(rows)
    return measurement.d1_mm, measurement.d2_mm
