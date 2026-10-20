"""The common middle: rows of on/off segments, and everything done to them —
spot compensation, mirror, polarity, affine transform (alignment, axis skew).
Everything below this layer works on segments, never on pixels."""
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
