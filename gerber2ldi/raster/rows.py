# gerber2ldi/raster/rows.py
"""Rasterisation by GEOMETRY: one machine row at a time (plan Ш2, design §3.3).

This module owns two rules from `map.md`:

  * "y строки = центр проезда лазера" — :func:`row_y`, one place;
  * "строка растра → отрезки" — the polygon/scan-line intersection.

No image is built here. A row is produced, wrapped in :class:`~gerber2ldi.scan.ScanRow`
and forgotten, so memory does not depend on the field size — measured in step 0:
44.8 MB at a 5 µm X step and 44.9 MB at 2.5 µm across a 100×100 mm field.

Note what is ABSENT on purpose: there is no X step. Drawing from geometry fixes
the X boundary exactly at the segment end; discretising X is the machine's job
(the G-code writes coordinates) and the preview's, not the input's. The 2.5 µm X
step belongs to the raster image, not to the geometry.

POLARITY is a sequence operation, not a boolean one: `%LPC*%` clears what was
drawn BEFORE it, so shapes are applied in file order (see `source.layer`).
"""
from __future__ import annotations

import math
from typing import Iterable, Iterator

from ..scan import ScanRow, Segment, normalize, subtract, union
from ..source import Shape

ROW_CENTRE = 0.5
"""A row is taken at the CENTRE of the machine pass, not at its edge."""

BBox = tuple[float, float, float, float]


def row_y(y0_mm: float, index: int, step_y_mm: float) -> float:
    """Height of row ``index`` — the single owner of ``y0 + (i + 0.5)·step``."""
    return y0_mm + (index + ROW_CENTRE) * step_y_mm


def row_count(height_mm: float, step_y_mm: float) -> int:
    """How many machine rows fit a figure ``height_mm`` tall."""
    if not math.isfinite(step_y_mm) or step_y_mm <= 0.0:
        raise ValueError(f"row step must be finite and positive, got {step_y_mm!r}")
    return int(round(height_mm / step_y_mm))


def _crossings(polygon: tuple[tuple[float, float], ...], y_mm: float) -> list[float]:
    """Half-open rule on the edges: a vertex on `y` is counted once, not twice."""
    xs: list[float] = []
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        if y1 == y2:
            continue
        if (y1 <= y_mm < y2) or (y2 <= y_mm < y1):
            xs.append(x1 + (y_mm - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return xs


def polygon_intervals(polygon: tuple[tuple[float, float], ...],
                      y_mm: float) -> tuple[Segment, ...]:
    """Where a closed polygon covers the line ``y = y_mm`` (even-odd rule)."""
    xs = _crossings(polygon, y_mm)
    return normalize((xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2))


def row_segments(shapes: Iterable[Shape], y_mm: float) -> tuple[Segment, ...]:
    """The row at ``y_mm``: shapes applied in FILE ORDER, dark adds, clear erases."""
    accumulated: tuple[Segment, ...] = ()
    for shape in shapes:
        _x0, y0, _x1, y1 = shape.bbox
        if not (y0 <= y_mm <= y1):
            continue
        intervals: tuple[Segment, ...] = ()
        for polygon in shape.polygons:
            intervals = union(intervals, polygon_intervals(polygon, y_mm))
        if not intervals:
            continue
        accumulated = (union(accumulated, intervals) if shape.dark
                       else subtract(accumulated, intervals))
    return accumulated


def iter_rows(shapes: Iterable[Shape], box: BBox, step_y_mm: float) -> Iterator[ScanRow]:
    """Every machine row of the field, geometrically, one at a time."""
    x0, y0, _x1, y1 = box
    shapes = tuple(shapes)
    for index in range(row_count(y1 - y0, step_y_mm)):
        height = row_y(y0, index, step_y_mm)
        yield ScanRow(height, row_segments(shapes, height))
