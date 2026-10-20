"""The common middle: rows of on/off segments, and the per-row work done to them —
spot compensation, X scaling, axis skew, mirror, polarity.

What lives here is ONLY what can be done one row at a time: everything in this
package changes X inside a row or moves whole rows along X.

What does NOT live here: rotation, ``scale_y`` and a general affine transform.
They change the ROW HEIGHT — a rotated figure cannot be produced by editing rows,
because rotation mixes rows into each other — so they belong to the input,
before rasterisation (contracts.md: "Поворот — на входе, не в отрезках").
The job layer (Ш4) assembles the whole calibration chain: rotation and scale_y at
the input, then compensation, skew, mirror and polarity from this package.

Everything below this layer works on segments, never on pixels.
"""
from .ops import intersect, invert, mirror, scale, shift, shrink, subtract, union
from .row import ScanRow, Segment, normalize
from .rows import GRID_TOLERANCE_MM, apply_skew, compensate

__all__ = [
    "GRID_TOLERANCE_MM",
    "ScanRow",
    "Segment",
    "apply_skew",
    "compensate",
    "intersect",
    "invert",
    "mirror",
    "normalize",
    "scale",
    "shift",
    "shrink",
    "subtract",
    "union",
]
