# gerber2ldi/patterns/square.py
"""The skew square and the formula that turns its diagonals into an angle.

Draw a square L×L as a CONTOUR, measure both diagonals with calipers, then

    theta = (d1 - d2) / (sqrt(2) * L)      [radians]

with L MEASURED, not nominal: with an uncalibrated Y scale the drawn square is not
the nominal size, and feeding the nominal side would hide that.

SIGN CONVENTION (design §17 and the calibration comments in the profile): theta > 0
means the real Y axis leans towards +X, i.e. ``x_real = x_commanded + y·tg(theta)``.
Then the corner at (0, L) is pushed to +x and the diagonal from the FRONT-LEFT
corner (0, 0) to the BACK-RIGHT corner (L, L) becomes the LONGER one:

    d1 = front-left  -> back-right
    d2 = front-right -> back-left

Swapping them flips the sign of the answer — that is the must-fail control of the
test, because a sign error here DOUBLES the error between the two sides of a
double-sided board instead of removing it (it was the finding of the Ш4 acceptance).

CALIBRATION IS NOT APPLIED by default: the square measures scale_x, scale_y and
skew, so drawing it through them would measure their residual. `without_calibration`
keeps the measured spot radius, so `--with-compensation` still has a number to work
with. `--with-calibration` keeps the profile's values, and then the measurement is a
RESIDUAL to be ADDED to them, not written over them.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable

from ..job import Machine
from ..scan import ScanRow
from .base import Pattern, calibration_note
from .shapes import layer_of, rectangle

DEFAULT_SIDE_MM = 150.0
DEFAULT_LINE_WIDTH_MM = 0.2


def without_calibration(machine: Machine) -> Machine:
    """The same machine with the measured scale and skew removed, the spot kept."""
    neutral = replace(machine.calibration, scale_x=1.0, scale_y=1.0, skew_deg=0.0)
    return replace(machine, calibration=neutral)


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
    notes = (
        f"pattern: skew square, side {side_mm:.3f} mm, line {line_width_mm:.3f} mm",
        "measure the diagonals: d1 front-left to back-right, d2 front-right to back-left",
        "theta = (d1 - d2) / (sqrt(2) * side measured with calipers)",
        calibration_note(drawn_with, applied=with_calibration),
    )
    return Pattern(layer=layer_of(shapes), machine=drawn_with, notes=notes)


def skew_from_diagonals(d1_mm: float, d2_mm: float, side_mm: float) -> float:
    """Skew in DEGREES, with the sign of the convention in the module docstring."""
    for name, value in (("d1", d1_mm), ("d2", d2_mm), ("side", side_mm)):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return math.degrees((d1_mm - d2_mm) / (math.sqrt(2.0) * side_mm))


def diagonals_from_rows(rows: Iterable[ScanRow]) -> tuple[float, float]:
    """(d1, d2) measured on DRAWN rows of the square contour.

    The corners are the ends of the outermost inked rows: the front bar spans the
    full width, so its row carries both front corners, and the back bar carries both
    back corners. Rows are taken at their CENTRES, half a row step inside the drawn
    band, which biases theta by about w/(2L) — 7e-5 deg at L = 150, two orders below
    what calipers resolve.
    """
    inked = [row for row in rows if row.segments]
    if len(inked) < 2:
        raise ValueError(f"need at least two inked rows, got {len(inked)}")
    front, back = inked[0], inked[-1]
    front_left, front_right = front.segments[0][0], front.segments[-1][1]
    back_left, back_right = back.segments[0][0], back.segments[-1][1]
    height = back.y_mm - front.y_mm
    return (math.hypot(back_right - front_left, height),
            math.hypot(front_right - back_left, height))
