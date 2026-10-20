# gerber2ldi/scan/row.py
"""One scan row — a height and the on-segments at that height.

This is the type everything below the input speaks (design §3.1). The invariant
that every consumer may rely on, and the ONLY place where it is enforced:

  * ``segments`` is a tuple of ``(x0, x1)`` in millimetres with ``x0 < x1``;
  * sorted by ``x0``;
  * no two segments touch or overlap — touching ones are merged.

Two rules that look like details and are not (Денис, 20.10):

  * a zero-length segment (``x1 == x0``) is DROPPED: erosion and clipping produce
    them legitimately, they carry no ink;
  * a REVERSED segment (``x1 < x0``) raises ``ValueError`` instead of being
    dropped. It can only come from a broken transformation — a naive mirror
    writes ``(2c - x0, 2c - x1)``, which flips every segment. Silently dropping
    those would turn the bottom layer into an empty file with no line in the log.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

Segment = tuple[float, float]


def normalize(segments: Iterable[Segment]) -> tuple[Segment, ...]:
    """Sort, merge touching/overlapping, drop zero-length; reject reversed/non-finite.

    The single owner of the row invariant: every operation in this package
    returns its result through this function (map: "нормализация строки отрезков").
    """
    prepared: list[Segment] = []
    for x0, x1 in segments:
        if not (math.isfinite(x0) and math.isfinite(x1)):
            # NaN compares False with everything, so a NaN would sail through the
            # "reversed" test, merge into a neighbour and reach the G-code as
            # `X nan`. Infinity would produce an unbounded segment. Both are
            # broken geometry from upstream, so both raise like a reversed one.
            raise ValueError(
                f"non-finite segment coordinate ({x0!r}, {x1!r}): the geometry "
                f"upstream is broken, not the data to be clipped")
        if x1 < x0:
            raise ValueError(
                f"reversed segment ({x0!r}, {x1!r}): a transformation is wrong, "
                f"not the data — fix the transformation, do not drop the segment")
        if x1 == x0:
            continue
        prepared.append((x0, x1))
    if not prepared:
        return ()
    prepared.sort()
    merged: list[list[float]] = [[prepared[0][0], prepared[0][1]]]
    for x0, x1 in prepared[1:]:
        if x0 <= merged[-1][1]:
            if x1 > merged[-1][1]:
                merged[-1][1] = x1
        else:
            merged.append([x0, x1])
    return tuple((x0, x1) for x0, x1 in merged)


@dataclass(frozen=True, slots=True)
class ScanRow:
    """Immutable: the segments are normalised once, in ``__post_init__``.

    ``y_mm`` is the CENTRE of the machine pass, ``y0 + (i + 0.5) * step_y`` —
    the raster layer decides that, this layer only carries it (design §3.2).
    """

    y_mm: float
    segments: tuple[Segment, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.y_mm):
            raise ValueError(f"row height must be finite, got {self.y_mm!r}")
        object.__setattr__(self, "segments", normalize(self.segments))

    @classmethod
    def empty(cls, y_mm: float) -> "ScanRow":
        """A row with no ink — the form used for rows outside the figure."""
        return cls(y_mm, ())

    @property
    def is_empty(self) -> bool:
        return not self.segments
