# gerber2ldi/source/layer.py
"""Input: a Gerber layer → flattened closed polygons in millimetres, FILE ORDER.

Why polygons and file order: the raster layer intersects scan rows with geometry,
and Gerber polarity is a sequence operation — a `%LPC*%` object erases what was
drawn BEFORE it. So the list order IS the drawing order and must not be sorted.

Units: everything leaving this module is millimetres as float (plan §2.2). gerbonara
keeps whatever the file says; `to_primitives(unit=MM)` converts once, here.

MEASURED LIBRARY BUG AND ITS WORKAROUND (step 0, probe_rotated_pads_2026_10_20):
gerbonara 1.6.3 `Rectangle.to_arc_poly()` (graphic_primitives.py:359) returns the
axis-aligned BOUNDING BOX of a rotated rectangle: a 2×1 pad turned 45° measured
4.5 mm² instead of 2.0. At multiples of 90° the bbox coincides with the real
shape, which is why the real board hides it. So rectangles are built here, from
their rotation, and never through that method.

STRAY LIBRARY OUTPUT: gerbonara prints `xy <x> <y>` to stdout from
aperture_macros/primitive.py:230 (a leftover debug print on macro primitive 5) and
emits Python warnings. Both are captured and logged at DEBUG, because the CLI's
stdout must carry only our own lines.
"""
from __future__ import annotations

import contextlib
import io
import logging
import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..errors import InputFileError
from ..i18n import _

logger = logging.getLogger(__name__)

ARC_MAX_ERROR_MM = 0.001
"""How far a flattened arc may deviate from the true circle, in millimetres. Ten
times finer than the finest raster step (2.5 µm) and 30 times finer than the
tightest realistic trace, so the error never decides a pixel."""

Point = tuple[float, float]
BBox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Shape:
    """One flattened object: closed polygons plus its polarity, in file order."""

    polygons: tuple[tuple[Point, ...], ...]
    dark: bool
    bbox: BBox


@dataclass(frozen=True, slots=True)
class Layer:
    """A loaded layer: geometry, its frame, and the few facts `info` prints."""

    shapes: tuple[Shape, ...]
    bbox: BBox
    unit: str
    aperture_count: int

    @property
    def has_clear(self) -> bool:
        """True when the layer erases something (`%LPC*%` present)."""
        return any(not shape.dark for shape in self.shapes)

    @property
    def size_mm(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (x1 - x0, y1 - y0)


@contextlib.contextmanager
def quiet_library():
    """Swallow a library's stray stdout and warnings; log both at DEBUG level.

    Process-wide by nature (`redirect_stdout` swaps `sys.stdout`), so this is for
    the single-threaded parse at the input boundary, not for concurrent use.
    """
    buffer = io.StringIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with contextlib.redirect_stdout(buffer):
            yield
    for item in caught:
        logger.debug("library warning: %s", item.message)
    stray = buffer.getvalue().strip()
    if stray:
        logger.debug("library stdout suppressed:\n%s", stray)


def _rectangle_polygon(prim) -> tuple[Point, ...]:
    """A Rectangle primitive → its four ROTATED corners (see the module docstring)."""
    cos, sin = math.cos(prim.rotation), math.sin(prim.rotation)
    half_w, half_h = prim.w / 2.0, prim.h / 2.0
    corners = ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h))
    return tuple((float(prim.x + x * cos - y * sin), float(prim.y + x * sin + y * cos))
                 for x, y in corners)


def _bbox_of(points: Iterable[Point]) -> BBox:
    xs = [x for x, _y in points]
    ys = [y for _x, y in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _shape_of(prim, *, index: int) -> Shape:
    """One gerbonara primitive → a Shape. Refuses loudly, never skips.

    A primitive we cannot draw is MISSING COPPER, and a debug line about it is not
    a substitute for a refusal: the board would come out with a pad erased and
    nobody would know. So both "the library raised" and "the outline is degenerate"
    are :class:`InputFileError`, naming the primitive kind and the OBJECT index in
    the file so the operator can find it. Measured on Денис's board (F_Cu, B_Cu,
    Edge_Cuts): 2699 primitives, none raises and none is degenerate — the strict
    refusal costs nothing on real input.
    """
    kind = type(prim).__name__
    if kind == "Rectangle":
        outline = _rectangle_polygon(prim)
        return Shape(polygons=(outline,), dark=bool(prim.polarity_dark),
                     bbox=_bbox_of(outline))
    try:
        arc_polygon = prim.to_arc_poly()
        outline = tuple((float(x), float(y))
                        for x, y in arc_polygon.approximate_arcs(
                            max_error=ARC_MAX_ERROR_MM).outline)
    except Exception as exc:  # noqa: BLE001 — the library's failure is our refusal
        raise InputFileError(
            _("Cannot draw {kind} of object #{index}: {error}")
            .format(kind=kind, index=index, error=exc)) from exc
    if len(outline) < 3:
        raise InputFileError(
            _("Object #{index} produced a {kind} without an outline")
            .format(index=index, kind=kind))
    dark = (bool(getattr(prim, "polarity_dark", True))
            and bool(getattr(arc_polygon, "polarity_dark", True)))
    return Shape(polygons=(outline,), dark=dark, bbox=_bbox_of(outline))


def load_layer(path: str | Path) -> Layer:
    """Read a Gerber layer. Raises :class:`InputFileError` when it cannot be read."""
    from gerbonara.rs274x import GerberFile
    from gerbonara.utils import MM

    source = Path(path)
    if not source.is_file():
        raise InputFileError(_("File not found: {path}").format(path=source))
    try:
        with quiet_library():
            gerber = GerberFile.open(str(source))
            shapes = tuple(_shape_of(prim, index=index)
                           for index, obj in enumerate(gerber.objects)
                           for prim in obj.to_primitives(unit=MM))
            (x0, y0), (x1, y1) = gerber.bounding_box(MM)
            unit = str(gerber.unit)
            aperture_count = len(list(gerber.apertures()))
    except InputFileError:
        raise
    except Exception as exc:  # noqa: BLE001 — the library's failure is our refusal
        raise InputFileError(_("Cannot read Gerber file {path}: {error}")
                             .format(path=source, error=exc)) from exc
    bbox = (float(x0), float(y0), float(x1), float(y1))
    return Layer(shapes=shapes, bbox=bbox, unit=unit, aperture_count=aperture_count)
