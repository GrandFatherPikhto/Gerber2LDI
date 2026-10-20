# gerber2ldi/patterns/shapes.py
"""Synthetic geometry for the patterns: rectangles as ordinary `Shape` polygons.

Nothing here knows about Gerber. The reader's `Shape` is just "closed polygons plus
polarity", which is exactly what a calibration figure is, so patterns reuse the type
and the whole downstream chain with it.
"""
from __future__ import annotations

from ..source import Layer, Shape

BBox = tuple[float, float, float, float]


def rectangle(x0_mm: float, y0_mm: float, x1_mm: float, y1_mm: float) -> Shape:
    """An axis-aligned filled rectangle, four corners in one polygon."""
    if not (x1_mm > x0_mm and y1_mm > y0_mm):
        raise ValueError(
            f"a rectangle needs x1 > x0 and y1 > y0, got "
            f"x {x0_mm!r} ... {x1_mm!r}, y {y0_mm!r} ... {y1_mm!r}")
    outline = ((x0_mm, y0_mm), (x1_mm, y0_mm), (x1_mm, y1_mm), (x0_mm, y1_mm))
    return Shape(polygons=(outline,), dark=True, bbox=(x0_mm, y0_mm, x1_mm, y1_mm))


def layer_of(shapes: tuple[Shape, ...]) -> Layer:
    """Shapes → a synthetic Layer; the frame is the union, computed once here."""
    if not shapes:
        raise ValueError("a pattern needs at least one shape")
    xs0 = min(shape.bbox[0] for shape in shapes)
    ys0 = min(shape.bbox[1] for shape in shapes)
    xs1 = max(shape.bbox[2] for shape in shapes)
    ys1 = max(shape.bbox[3] for shape in shapes)
    bbox: BBox = (xs0, ys0, xs1, ys1)
    return Layer(shapes=shapes, bbox=bbox, unit="mm", aperture_count=0)
