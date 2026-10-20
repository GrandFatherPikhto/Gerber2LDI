# gerber2ldi/patterns/bars.py
"""Test bars: lines and gaps of known widths, to see the spot and the resolution.

There is no caption on the board, so the LAYOUT IS THE LABEL and its order is fixed:

  * a block of HORIZONTAL groups: five lines along X, each ``w`` tall with a gap of
    ``w`` under it. These measure the resolution ALONG Y — the spot plus the row
    step, the two things that decide whether a 50 µm gap survives;
  * a block of VERTICAL groups to the right: five lines along Y, each ``w`` wide with
    a gap of ``w``. These measure the resolution ACROSS X — where the spot edges and
    the PWM dashes show.

Groups follow the order of ``widths`` (default 200, 150, 100, 80, 60, 50 µm) with a
gap of three widths between groups; the blocks are ``BLOCK_GAP_MM`` apart and the
figure starts at the origin. Spot compensation is OFF for this pattern by default
(`--with-compensation` turns it on): the bars are what the spot is MEASURED from, so
requiring a measurement first would be a closed circle.
"""
from __future__ import annotations

from ..i18n import _
from ..job import Machine
from ..source import Shape
from .base import Pattern, calibration_note
from .shapes import layer_of, rectangle

DEFAULT_WIDTHS_MM = (0.200, 0.150, 0.100, 0.080, 0.060, 0.050)
LINES_PER_GROUP = 5
GROUP_GAP_FACTOR = 3.0
BLOCK_GAP_MM = 2.0
DEFAULT_LENGTH_MM = 20.0


def bars(machine: Machine, *, widths: tuple[float, ...] = DEFAULT_WIDTHS_MM,
         length_mm: float = DEFAULT_LENGTH_MM) -> Pattern:
    """The bars pattern for `machine`; the profile's calibration IS applied here."""
    widths = tuple(float(width) for width in widths)
    if not widths:
        raise ValueError("at least one bar width is required")
    if any(not width > 0.0 for width in widths):
        raise ValueError(f"bar widths must be positive, got {widths!r}")
    if not length_mm > 0.0:
        raise ValueError(f"bar length must be positive, got {length_mm!r}")

    pitch = (2 * LINES_PER_GROUP - 1 + GROUP_GAP_FACTOR)
    shapes: list[Shape] = []

    # Horizontal groups: lines along X, stacked in Y from the origin.
    y = 0.0
    for width in widths:
        for line in range(LINES_PER_GROUP):
            y0 = y + line * 2.0 * width
            shapes.append(rectangle(0.0, y0, length_mm, y0 + width))
        y += pitch * width

    # Vertical groups: lines along Y, stacked in X after the first block.
    x = length_mm + BLOCK_GAP_MM
    for width in widths:
        for line in range(LINES_PER_GROUP):
            x0 = x + line * 2.0 * width
            shapes.append(rectangle(x0, 0.0, x0 + width, length_mm))
        x += pitch * width

    step = machine.scan.row_step_mm
    warnings = tuple(
        _("Bar width {width} mm is below the row step {step} mm: it may print as one row or vanish")
        .format(width=width, step=step)
        for width in widths if width < step)

    notes = (
        f"pattern: test bars, {LINES_PER_GROUP} lines per group, gap = line width",
        "groups, fixed order: horizontal along X (Y resolution), then vertical across (X resolution)",
        "widths in order, mm: " + " ".join(f"{width:.3f}" for width in widths),
        calibration_note(machine, applied=True),
    )
    return Pattern(layer=layer_of(tuple(shapes)), machine=machine,
                   notes=notes, warnings=warnings)
