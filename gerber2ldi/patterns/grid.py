# gerber2ldi/patterns/grid.py
"""The grid: a rectangle filled with lines, to see that the WHOLE area gets exposed.

Денис, 22.10: «указываю прямоугольник, толщину линии и расстояние между линиями.
Например, 0.2 мм толщина линии, расстояние 0.15. И весь прямоугольник заполнить (это
у меня тест на напыление)».

LAYOUT (the only rule, and it is the whole pattern):

  * period = line + gap;
  * the first line is flush with the near edge (left for lines along Y, bottom for
    lines along X), line ``i`` occupies ``[i·period, i·period + line]``;
  * the next line is added only while it fits ENTIRELY inside the rectangle, so the
    leftover at the far edge is a GAP, never a sliver of line;
  * ``x`` draws lines ALONG X (horizontal, stacked in Y), ``y`` draws them along Y
    (vertical, stacked in X), ``both`` draws the union — the crossings are copper, and
    the union is the same boolean ∪ the rest of the chain uses.

WHAT IS QUANTISED, AND WHERE. A line along Y has an exact width in X: the raster does
not discretise X, so 0.200 mm is commanded as 0.200 mm (its edge snaps to the X motor
step, which the job counts and announces). A line along X is a BAND OF ROWS: its width
and the gap above it can only be a whole number of ``scan.row_step_um``, so 0.21 mm on
a 0.025 mm row step comes out 0.200 or 0.225. That is said out loud as a warning — the
picture is still drawn, the operator just has to read the number he will measure.

Spot compensation is OFF by default, like the other patterns: a grid drawn through a
radius nobody measured would hide exactly the over-exposure the grid exists to show.
"""

from __future__ import annotations

import math

from ..i18n import _
from ..job import Machine
from ..source import Shape
from .base import Pattern, calibration_note
from .shapes import layer_of, rectangle

DEFAULT_GRID_WIDTH_MM = 50.0
DEFAULT_GRID_HEIGHT_MM = 40.0
DEFAULT_GRID_LINE_MM = 0.2
DEFAULT_GRID_GAP_MM = 0.15

GRID_DIRECTIONS = ("both", "x", "y")
"""``x`` — lines along X, ``y`` — along Y, ``both`` — the union of the two."""

GRID_TOLERANCE_MM = 1e-6
"""How far a line may stand off the edge and still count as fitting, in millimetres.

Not the row-grid tolerance of `scan.rows` (that one guards the shape of a row); this
one only absorbs float noise in ``extent − k·period``, so that a rectangle measured as
exactly ``n·period − gap`` keeps its last line instead of losing it to 1e-16.
"""


def _positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"the {name} must be finite and positive, got {value!r}")
    return value


def fit_lines(extent_mm: float, line_mm: float, gap_mm: float) -> int:
    """How many whole lines fit ``extent_mm``, the first one flush with the near edge.

    Line ``i`` sits at ``i·(line + gap)``; it fits while ``i·period + line ≤ extent``.
    Zero means the extent holds no line at all — the caller decides whether that is a
    refusal (a direction that is being drawn) or just a count of nothing.
    """
    _positive("line", line_mm)
    _positive("gap", gap_mm)
    if not math.isfinite(extent_mm):
        raise ValueError(f"the extent must be finite, got {extent_mm!r}")
    if extent_mm + GRID_TOLERANCE_MM < line_mm:
        return 0
    period = line_mm + gap_mm
    steps = (extent_mm - line_mm + GRID_TOLERANCE_MM) / period
    return int(math.floor(steps)) + 1


def _snapped(value_mm: float, step_mm: float) -> tuple[float, float] | None:
    """The two widths the row grid can produce, or ``None`` when ``value`` already fits."""
    ratio = value_mm / step_mm
    if abs(ratio - round(ratio)) * step_mm <= GRID_TOLERANCE_MM:
        return None
    return round(math.floor(ratio) * step_mm, 6), round(math.ceil(ratio) * step_mm, 6)


def grid(machine: Machine, *, width_mm: float = DEFAULT_GRID_WIDTH_MM,
         height_mm: float = DEFAULT_GRID_HEIGHT_MM,
         line_mm: float = DEFAULT_GRID_LINE_MM,
         gap_mm: float = DEFAULT_GRID_GAP_MM,
         direction: str = "both") -> Pattern:
    """Lines of `line_mm` with `gap_mm` between them, filling the rectangle at the origin."""
    if direction not in GRID_DIRECTIONS:
        raise ValueError(
            f"direction must be one of {', '.join(GRID_DIRECTIONS)}, got {direction!r}")
    _positive("rectangle width", width_mm)
    _positive("rectangle height", height_mm)
    _positive("line", line_mm)
    _positive("gap", gap_mm)

    period = line_mm + gap_mm
    along_x = direction in ("both", "x")   # horizontal lines, stacked in Y
    along_y = direction in ("both", "y")   # vertical lines, stacked in X
    count_y = fit_lines(height_mm, line_mm, gap_mm)
    count_x = fit_lines(width_mm, line_mm, gap_mm)
    if along_x and count_y == 0:
        raise ValueError(
            f"the {height_mm!r} mm side of the rectangle holds no line {line_mm!r} mm tall")
    if along_y and count_x == 0:
        raise ValueError(
            f"the {width_mm!r} mm side of the rectangle holds no line {line_mm!r} mm wide")

    shapes: list[Shape] = []
    if along_x:
        for index in range(count_y):
            y0 = index * period
            shapes.append(rectangle(0.0, y0, width_mm, y0 + line_mm))
    if along_y:
        for index in range(count_x):
            x0 = index * period
            shapes.append(rectangle(x0, 0.0, x0 + line_mm, height_mm))

    # Warnings, not refusals: the drawing is still what the operator asked for, but the
    # row grid will round the BAND widths, and a test pattern that lies about its own
    # numbers is worse than no test pattern.
    step = machine.scan.row_step_mm
    warnings: list[str] = []
    if along_x:
        snapped = _snapped(line_mm, step)
        if snapped is not None:
            warnings.append(
                _("The line {value} mm across Y comes out {low} or {high} mm: the row step is {step} mm")
                .format(value=round(line_mm, 4), low=snapped[0], high=snapped[1],
                        step=round(step, 4)))
        snapped = _snapped(gap_mm, step)
        if snapped is not None:
            warnings.append(
                _("The gap {value} mm across Y comes out {low} or {high} mm: the row step is {step} mm")
                .format(value=round(gap_mm, 4), low=snapped[0], high=snapped[1],
                        step=round(step, 4)))

    notes = (
        f"pattern: grid, {width_mm:.3f} x {height_mm:.3f} mm, line {line_mm:.3f} mm, "
        f"gap {gap_mm:.3f} mm, period {period:.3f} mm",
        f"grid: {count_y if along_x else 0} lines along X, "
        f"{count_x if along_y else 0} lines along Y, direction {direction}",
        "layout: the first line is flush with the near edge, the leftover at the far "
        "edge is a gap; the lines are rectangles through the ordinary job",
        calibration_note(machine, applied=True),
    )
    return Pattern(layer=layer_of(tuple(shapes)), machine=machine, notes=notes,
                   warnings=tuple(warnings))
