# gerber2ldi/scan/rows.py
"""Whole-row sequences: spot compensation and axis skew (plan Ш1).

WHAT DOES NOT LIVE HERE, and why: rotation and ``scale_y`` change the ROW HEIGHT,
so they belong to the input, before rasterisation — a rotated figure cannot be
obtained by editing rows, because rotation mixes rows into each other. Only
per-row X operations may live below the input (contracts.md: "Поворот — на входе,
не в отрезках"). The job (Ш4) assembles the whole calibration chain: scale_y and
rotation at the input, then compensation, skew, mirror, polarity.

MEMORY: the compensation keeps a window of ``±floor(r / step_y)`` rows and never
the layer; a 5 000-row layer and a 50 000-row layer cost the same (measured in
step 0: candidate A holds 44.9 MB at both 5 µm and 2.5 µm across).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Iterable, Iterator

from .ops import intersect, shift, shrink
from .row import Segment, ScanRow

GRID_TOLERANCE_MM = 1e-6
"""How far a row height may sit off the grid, in millimetres.

Needed because the raster computes ``y = y0 + (i + 0.5) * step_y`` in float, and
e.g. 1/3 mm at a 20 µm step does not land exactly. A micrometre is three orders
of magnitude below any mechanical meaning and four above float noise, so it
separates "float rounding" from "the caller mixed two grids" without guesswork.
"""


def _grid_index(y_mm: float, y0_mm: float, step_y_mm: float) -> int:
    steps = (y_mm - y0_mm) / step_y_mm
    index = round(steps)
    if abs(steps - index) * step_y_mm > GRID_TOLERANCE_MM:
        raise ValueError(
            f"row y={y_mm!r} is not on the grid y0={y0_mm!r}, step={step_y_mm!r} "
            f"(off by {abs(steps - index) * step_y_mm:.3g} mm, "
            f"tolerance {GRID_TOLERANCE_MM:g} mm)")
    return int(index)


def _dense(rows: Iterable[ScanRow], step_y_mm: float
           ) -> Iterator[tuple[int, float, tuple[Segment, ...]]]:
    """``(index, y, segments)`` for every grid row between the first and the last
    one given, with the gaps filled by EMPTY rows.

    A caller may therefore emit only the rows that carry ink (the raster does):
    "rows outside the figure are empty" is handled here, once, instead of in every
    consumer.
    """
    iterator = iter(rows)
    first = next(iterator, None)
    if first is None:
        return
    y0_mm = first.y_mm
    previous = _grid_index(y0_mm, y0_mm, step_y_mm)
    if previous != 0:
        raise ValueError("internal: the first row defines the grid origin")
    yield previous, y0_mm, first.segments
    for row in iterator:
        index = _grid_index(row.y_mm, y0_mm, step_y_mm)
        if index <= previous:
            raise ValueError(
                f"rows must be strictly increasing in y: {row.y_mm!r} after "
                f"index {previous} (grid origin {y0_mm!r}, step {step_y_mm!r})")
        while previous + 1 < index:
            previous += 1
            yield previous, y0_mm + previous * step_y_mm, ()
        previous = index
        yield index, row.y_mm, row.segments


def compensate(rows: Iterable[ScanRow], step_y_mm: float,
               radius_mm: float) -> Iterator[ScanRow]:
    """Erode every row by the measured spot radius (design §3.4).

    A row ``y`` becomes the intersection, over ``|dy| <= r``, of the rows at
    ``y + dy`` narrowed by ``sqrt(r² - dy²)``. At ``|dy| == r`` the narrowing is
    zero and the row still constrains the result — that is the formula, not an
    edge case to optimise away.

    ``radius_mm == 0`` returns the rows unchanged. Rows beyond the given ones are
    empty, so the figure loses ``r`` at its top and bottom edges, as it must.
    """
    if not math.isfinite(step_y_mm) or step_y_mm <= 0.0:
        raise ValueError(f"row step must be finite and positive, got {step_y_mm!r}")
    if not math.isfinite(radius_mm) or radius_mm < 0.0:
        raise ValueError(f"spot radius must be finite and non-negative, got {radius_mm!r}")

    half_window = int(math.floor(radius_mm / step_y_mm + GRID_TOLERANCE_MM))
    # Shrink amount per offset in rows; built once, not per row.
    shrinks = [math.sqrt(max(0.0, radius_mm * radius_mm - (j * step_y_mm) ** 2))
               for j in range(-half_window, half_window + 1)]

    y0_mm: float | None = None
    emitted = -1
    last_index = -1
    # (index, y, segments) — y is carried along so that an INPUT row keeps the
    # caller's exact height instead of being rebuilt as y0 + i*step (which drifts
    # in the last bits and would make an identity run non-identical).
    window: deque[tuple[int, float, tuple[Segment, ...]]] = deque(
        maxlen=2 * half_window + 1)

    for index, y_mm, segments in _dense(rows, step_y_mm):
        if y0_mm is None:
            y0_mm = y_mm
        window.append((index, y_mm, segments))
        last_index = index
        # Row `index - half_window` now has its whole window in hand.
        while emitted < index - half_window:
            emitted += 1
            yield _eroded_row(window, emitted, half_window, shrinks)

    if y0_mm is not None:
        while emitted < last_index:
            emitted += 1
            yield _eroded_row(window, emitted, half_window, shrinks)


def _eroded_row(window: deque, target: int, half_window: int,
                shrinks: list[float]) -> ScanRow:
    """The compensated row `target`, from the rows currently in the window.

    `target` is always in the window when this is called (the emit loop runs at
    most `half_window` rows behind the read head), so its own height is known;
    only its NEIGHBOURS may be missing, and a missing neighbour is an empty row.
    """
    present = {index: (y_mm, segments) for index, y_mm, segments in window}
    y_mm = present[target][0]
    result: tuple[Segment, ...] | None = None
    for offset in range(-half_window, half_window + 1):
        entry = present.get(target + offset)
        if entry is None:
            return ScanRow(y_mm, ())
        narrowed = shrink(entry[1], shrinks[offset + half_window])
        result = narrowed if result is None else intersect(result, narrowed)
        if not result:
            return ScanRow(y_mm, ())
    return ScanRow(y_mm, () if result is None else result)


def apply_skew(rows: Iterable[ScanRow], tan_theta: float) -> Iterator[ScanRow]:
    """Shift each row by ``y · tg θ`` — the measured non-perpendicularity (design §17).

    ``y`` is the row's own height, so the origin decides where the figure pivots:
    the input already put the figure where the machine will draw it. ``tan`` is
    taken outside (``tan(radians(skew_deg))``) so this layer never converts units.
    """
    if not math.isfinite(tan_theta):
        raise ValueError(f"skew tangent must be finite, got {tan_theta!r}")
    for row in rows:
        if row.is_empty:
            yield row
            continue
        yield ScanRow(row.y_mm, shift(row.segments, row.y_mm * tan_theta))
