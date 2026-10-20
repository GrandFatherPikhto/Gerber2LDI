# gerber2ldi/scan/ops.py
"""Pure operations on ONE row's segments (plan Ш1).

Everything here takes segments and returns segments; nothing mutates its input and
nothing knows about pixels. Inputs are pushed through :func:`row.normalize`
defensively — it is idempotent, so a caller cannot corrupt a result by handing in
raw data, and there is still exactly one implementation of the invariant.

Why these and not more: this is the whole vocabulary the pipeline needs below the
input (design §3.1) — union/subtract/intersect are the Gerber dark/clear polarity,
mirror is the bottom layer, invert is the process polarity, shrink is one step of
the spot compensation, scale is the measured axis calibration.
"""
from __future__ import annotations

import math
from typing import Iterable

from .row import Segment, normalize


def union(a: Iterable[Segment], b: Iterable[Segment]) -> tuple[Segment, ...]:
    """Everything that is "on" in either list."""
    return normalize(tuple(normalize(a)) + tuple(normalize(b)))


def subtract(a: Iterable[Segment], b: Iterable[Segment]) -> tuple[Segment, ...]:
    """``a - b``: what stays on after the second list is cleared away."""
    remaining = tuple(normalize(b))
    out: list[Segment] = []
    for x0, x1 in normalize(a):
        pieces: list[Segment] = [(x0, x1)]
        for c0, c1 in remaining:
            if not pieces:
                break
            cut: list[Segment] = []
            for p0, p1 in pieces:
                if c1 <= p0 or c0 >= p1:
                    cut.append((p0, p1))
                    continue
                if p0 < c0:
                    cut.append((p0, c0))
                if c1 < p1:
                    cut.append((c1, p1))
            pieces = cut
        out.extend(pieces)
    return normalize(out)


def intersect(a: Iterable[Segment], b: Iterable[Segment]) -> tuple[Segment, ...]:
    """What is "on" in both lists — the erosion step of the spot compensation."""
    left, right = tuple(normalize(a)), tuple(normalize(b))
    out: list[Segment] = []
    i = j = 0
    while i < len(left) and j < len(right):
        x0 = max(left[i][0], right[j][0])
        x1 = min(left[i][1], right[j][1])
        if x0 < x1:
            out.append((x0, x1))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return normalize(out)


def shift(segments: Iterable[Segment], dx: float) -> tuple[Segment, ...]:
    """Move the whole row along X (axis skew, alignment offset)."""
    if not math.isfinite(dx):
        raise ValueError(f"shift must be finite, got {dx!r}")
    return normalize((x0 + dx, x1 + dx) for x0, x1 in normalize(segments))


def mirror(segments: Iterable[Segment], axis_x: float) -> tuple[Segment, ...]:
    """Reflect about the vertical line ``x = axis_x`` (the bottom layer).

    The order inside the pair is flipped ON PURPOSE: ``(2c - x1, 2c - x0)``.
    Writing ``(2c - x0, 2c - x1)`` produces reversed segments, which
    :func:`row.normalize` rejects instead of silently emptying the row.
    """
    if not math.isfinite(axis_x):
        raise ValueError(f"mirror axis must be finite, got {axis_x!r}")
    return normalize((2.0 * axis_x - x1, 2.0 * axis_x - x0)
                     for x0, x1 in normalize(segments))


def invert(segments: Iterable[Segment], xmin: float, xmax: float) -> tuple[Segment, ...]:
    """Flip polarity inside ``[xmin, xmax]``: on becomes off and back.

    The bounds are explicit because "inside what" is the JOB's decision (process
    polarity, Ш4), not this layer's.
    """
    if not (math.isfinite(xmin) and math.isfinite(xmax)):
        raise ValueError(f"invert bounds must be finite, got ({xmin!r}, {xmax!r})")
    if xmax < xmin:
        raise ValueError(f"invert bounds reversed: xmin={xmin!r} > xmax={xmax!r}")
    out: list[Segment] = []
    cursor = xmin
    for x0, x1 in normalize(segments):
        if x1 <= xmin or x0 >= xmax:
            continue
        lo, hi = max(x0, xmin), min(x1, xmax)
        if lo > cursor:
            out.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < xmax:
        out.append((cursor, xmax))
    return normalize(out)


def shrink(segments: Iterable[Segment], d: float) -> tuple[Segment, ...]:
    """Narrow every segment by ``d`` on both sides; vanished ones are dropped.

    Dropping happens HERE, before a ``ScanRow`` is built, so the zero-length rule
    of :func:`row.normalize` is never the thing that hides a bug.
    """
    if not math.isfinite(d):
        raise ValueError(f"shrink distance must be finite, got {d!r}")
    if d < 0:
        raise ValueError(f"shrink distance must be non-negative, got {d!r}")
    if d == 0.0:
        return normalize(segments)
    out = [(x0 + d, x1 - d) for x0, x1 in normalize(segments) if x1 - x0 > 2.0 * d]
    return normalize(out)


def scale(segments: Iterable[Segment], factor: float) -> tuple[Segment, ...]:
    """Multiply X about the origin — the measured ``scale_x`` (design §17).

    Only X: ``scale_y`` and rotation change the ROW HEIGHT, so they act on the
    input, before rasterisation (plan Ш1). A non-positive factor is a profile
    error, not a mirror request — mirroring is :func:`mirror`.
    """
    if not math.isfinite(factor) or factor <= 0.0:
        raise ValueError(f"scale factor must be finite and positive, got {factor!r}")
    if factor == 1.0:
        return normalize(segments)
    return normalize((x0 * factor, x1 * factor) for x0, x1 in normalize(segments))
