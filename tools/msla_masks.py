#!/usr/bin/env python3
# tools/msla_masks.py
"""Drawing layer of `tools/msla_tests.py`: screen pixels, the marker, the PNG.

SPLIT (Денис, 23.10.2026): the tool grew past 400 lines of code, so it is two files.
This one knows how to turn millimetres into WHOLE pixels of the screen, how to put a
figure on the screen, how to mark it and how to write the PNG. The PATTERNS and the
command line live in `msla_tests.py`; that is the one to run:

    .venv/bin/python tools/msla_tests.py --help

THE PIXEL RULE, and it is the whole reason this file exists: `n = round(size / pitch)`
pixels of THAT axis. `n = 0` is a refusal — a line thinner than a pixel does not exist —
and rounding, not truncation, keeps a drawn line at the nearest pixel edge instead of
making every dimension thinner than asked.

`msla_tests.py` imports `_quantised` and `_actual` on purpose: the two files are ONE
tool, and the leading underscore means "not for the operator", not "not for the other
half". Nothing here knows what a test pattern is.

Standard library + `numpy` + `Pillow` only; `gerber2ldi` is NOT imported, so this works
where the laser package cannot even be installed. Messages are ASCII English ("um", not
"µm") because they also travel into the PNG text chunk.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

WHITE = 255
UM_PER_MM = 1000.0
REPORT_KEY = "msla_tests"

MARKER_MM = 2.0
"""Side of the marker square in millimetres. Its pixel count follows each axis."""
MARKER_LINE_PX = 3
"""Stem and arm width of the marker in PIXELS: below ~100 um a line disappears in development."""
MARKER_GAP_MM = 1.0
"""Gap between the marker and the pattern, in millimetres — they must not touch."""


class Refusal(Exception):
    """The operator asked for something that cannot be drawn; the message says why."""


@dataclass(frozen=True, slots=True)
class Screen:
    """The LCD in pixels, and the size of ONE pixel on each axis in micrometres."""

    width_px: int
    height_px: int
    pixel_x_um: float
    pixel_y_um: float

    @property
    def pixel_x_mm(self) -> float:
        return self.pixel_x_um / UM_PER_MM

    @property
    def pixel_y_mm(self) -> float:
        return self.pixel_y_um / UM_PER_MM

    def mm_x(self, pixels: int) -> float:
        return pixels * self.pixel_x_mm

    def mm_y(self, pixels: int) -> float:
        return pixels * self.pixel_y_mm


@dataclass(frozen=True, slots=True)
class Figure:
    """A drawing in its own pixels, plus the report lines that belong to it."""

    pixels: np.ndarray
    report: tuple[str, ...]

    @property
    def width_px(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height_px(self) -> int:
        return int(self.pixels.shape[0])


# ───────────────────────────── pixel binding ───────────────────────────────

def _quantised(size_mm: float, pitch_mm: float, axis: str, what: str,
               *, needed: bool = True) -> int:
    """A size in WHOLE pixels of one axis; ``needed`` and zero is a refusal.

    Rounding, not truncation: `round` is the nearest pixel edge, `int` would bias every
    dimension downwards and make a 0.06 mm line thinner than asked on both axes.
    """
    pixels = int(round(size_mm / pitch_mm))
    if needed and pixels < 1:
        raise Refusal(f"{what} {size_mm:.3f} mm is below one "
                      f"{pitch_mm * UM_PER_MM:.1f} um pixel along {axis}")
    return pixels


def _actual(pixels: int, pitch_mm: float, axis: str, *, needed: bool = True) -> str:
    """One axis of a request, as it came out: pixels and their real size in micrometres."""
    if not needed:
        return f"{axis} not used in this direction"
    return f"{pixels} px along {axis} = {pixels * pitch_mm * UM_PER_MM:.1f} um"


# ───────────────────────────────── marker ──────────────────────────────────

def marker_pixels(screen: Screen) -> np.ndarray:
    """The letter F in whole pixels: stem on the left, arms to the top-right.

    Asymmetric in BOTH axes on purpose — that is the whole point of it: a mirrored
    exposure puts the stem on the right, an upside-down one puts the arms at the bottom,
    and both are read off the developed plate by eye, without a microscope.
    """
    width = _quantised(MARKER_MM, screen.pixel_x_mm, "X", "the marker side")
    height = _quantised(MARKER_MM, screen.pixel_y_mm, "Y", "the marker side")
    if width < 3 * MARKER_LINE_PX or height < 2 * MARKER_LINE_PX:
        raise Refusal(
            f"the pixel is too coarse for the marker: a {MARKER_MM:.1f} mm side is "
            f"{width} x {height} px, the F needs {3 * MARKER_LINE_PX} px across and "
            f"{2 * MARKER_LINE_PX} px down of {MARKER_LINE_PX} px lines (use --no-marker)")
    pixels = np.zeros((height, width), dtype=np.uint8)
    pixels[:, 0:MARKER_LINE_PX] = WHITE                     # the stem
    pixels[0:MARKER_LINE_PX, 0:width] = WHITE               # the top arm, full length
    middle = height // 2
    arm = max(MARKER_LINE_PX, width * 7 // 10)              # the middle arm, shorter
    pixels[middle:middle + MARKER_LINE_PX, 0:arm] = WHITE
    return pixels


def add_marker(screen: Screen, figure: Figure) -> Figure:
    """Put the F at the top-left corner of the figure; the pattern moves right of it.

    The pattern keeps its own geometry — same rows, same blocks, same widths — it only
    moves. A marker drawn ON the pattern would corrupt exactly what the pattern measures.
    """
    marker = marker_pixels(screen)
    marker_height, marker_width = int(marker.shape[0]), int(marker.shape[1])
    gap = _quantised(MARKER_GAP_MM, screen.pixel_x_mm, "X", "the marker gap")
    pixels = np.zeros((max(figure.height_px, marker_height),
                       marker_width + gap + figure.width_px), dtype=np.uint8)
    pixels[0:marker_height, 0:marker_width] = marker
    pixels[0:figure.height_px, marker_width + gap:] = figure.pixels
    report = figure.report + (
        f"marker: the letter F in the top-left corner of the figure, {marker_width} x "
        f"{marker_height} px = {screen.mm_x(marker_width):.3f} x "
        f"{screen.mm_y(marker_height):.3f} mm, line {MARKER_LINE_PX} px; the pattern "
        f"starts {gap} px = {screen.mm_x(gap):.3f} mm to its right",
        "marker: on a straight exposure the stem is on the LEFT and the arms point to the "
        "top-right; a mirrored one puts the stem on the RIGHT, an upside-down one puts the "
        "arms at the bottom",
    )
    return Figure(pixels=pixels, report=report)


# ──────────────────────────── placement and output ─────────────────────────

def compose(screen: Screen, figure: Figure, at_mm: tuple[float, float] | None,
            *, mirror: bool, invert: bool) -> tuple[np.ndarray, tuple[str, ...]]:
    """Paste the figure onto the screen, then mirror and invert the WHOLE picture."""
    if at_mm is None:
        x_px = (screen.width_px - figure.width_px) // 2
        y_px = (screen.height_px - figure.height_px) // 2
        where = "centred"
    else:
        x_px = int(round(at_mm[0] / screen.pixel_x_mm))
        y_px = int(round(at_mm[1] / screen.pixel_y_mm))
        where = f"at {at_mm[0]:.3f}, {at_mm[1]:.3f} mm from the top-left pixel"
    inside = (0 <= x_px and 0 <= y_px
              and x_px + figure.width_px <= screen.width_px
              and y_px + figure.height_px <= screen.height_px)
    if not inside:
        raise Refusal(
            f"the figure {screen.mm_x(figure.width_px):.3f} x "
            f"{screen.mm_y(figure.height_px):.3f} mm ({figure.width_px} x "
            f"{figure.height_px} px) does not fit the {screen.width_px} x "
            f"{screen.height_px} px screen {where}: it would span {x_px} ... "
            f"{x_px + figure.width_px} px across and {y_px} ... "
            f"{y_px + figure.height_px} px down")

    image = np.zeros((screen.height_px, screen.width_px), dtype=np.uint8)
    image[y_px:y_px + figure.height_px, x_px:x_px + figure.width_px] = figure.pixels

    placed = [
        f"figure: {screen.mm_x(figure.width_px):.3f} x {screen.mm_y(figure.height_px):.3f} mm "
        f"({figure.width_px} x {figure.height_px} px), {where} ({x_px}, {y_px} px)",
    ]
    if mirror:
        image = np.fliplr(image)
        flipped = screen.width_px - x_px - figure.width_px
        placed.append(f"mirror: on (the whole picture is reflected along X: on the plate the "
                      f"figure sits at {flipped} px across = {screen.mm_x(flipped):.3f} mm)")
    else:
        placed.append("mirror: off")
    if invert:
        image = WHITE - image
        placed.append("polarity: white = the gaps (inverted: the resist keeps what was NOT lit)")
    else:
        placed.append("polarity: white = the lines (a negative resist keeps what was lit)")
    return image, tuple(placed)


def write_mask(path: str | Path, screen: Screen, figure: Figure,
               image: np.ndarray, lines: tuple[str, ...]) -> Path:
    """Write the PNG (mode L, exactly the screen) with the report in a text chunk."""
    report = (
        "msla_tests: a test mask for an MSLA printer",
        f"screen: {screen.width_px} x {screen.height_px} px",
        f"pixel: {screen.pixel_x_um:.2f} x {screen.pixel_y_um:.2f} um",
        *figure.report,
        *lines,
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    info.add_text(REPORT_KEY, "\n".join(report))
    Image.fromarray(image, mode="L").save(target, format="PNG", pnginfo=info)
    return target
