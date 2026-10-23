#!/usr/bin/env python3
# tools/msla_tests.py
"""Draw test masks for an MSLA printer (LCD + 405 nm UV), in exact screen pixels.

The printer exposes a WHOLE LAYER through an LCD, so the mask decides the result, and an
LCD can only light whole pixels: a test pattern has to be drawn in pixels and has to say
what each requested width actually became. This file is the PATTERNS and the command
line; turning millimetres into whole pixels, the letter F that proves the exposure is not
mirrored, the placement and the PNG live in `msla_masks.py`.

PATTERNS (the layout is the label: there is no caption on a mask)

  bars — groups of `--lines` lines, the gap equal to the line width, in the order of
         `--widths-mm`: a horizontal block (lines along X, so the Y resolution shows)
         and a vertical block to its right (lines along Y, the X resolution). Each
         block is SQUARE: a group measures the same physical distance both ways, which
         is what makes two axes comparable.
  grid — a rectangle filled with lines: the first line is flush with the near edge, a
         line is drawn only WHOLE, the leftover at the far edge is a gap, and `both`
         draws the union of the two directions — the same rule as `patterns/grid.py`.

Usage:
    .venv/bin/python tools/msla_tests.py bars --screen-px 5760x3600 \\
        --pixel-um 34x34 -o out/bars.png \\
        [--widths-mm 0.2,0.15,0.1,0.08,0.06,0.05] [--lines 5] \\
        [--at X,Y] [--mirror] [--invert] [--marker|--no-marker]
    .venv/bin/python tools/msla_tests.py grid --screen-px 5760x3600 \\
        --pixel-um 34x34 -o out/grid.png \\
        [--width-mm 50 --height-mm 40 --line-mm 0.2 --gap-mm 0.15] \\
        [--direction both|x|y] [--at X,Y] [--mirror] [--invert] [--marker|--no-marker]

`--screen-px` and `--pixel-um` are REQUIRED and have no defaults: an invented number is
worse than a refusal. For the Anycubic Photon Mono X 6Ks the printer profile in UVtools
(and PrusaSlicer's `Anycubic Photon Mono X 6Ks.ini`) says 195.84 x 122.4 mm over
5760 x 3600 px, i.e. 34 um per pixel — that is a profile, not a verified spec sheet.

The report printed on stdout — widths requested and ACTUAL, plus the lines that
`msla_masks` adds — is also written into the PNG text chunk `msla_tests`, so a file found
a month later still says what it is.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np

from msla_masks import (WHITE, Figure, Refusal, Screen, _actual, _quantised, add_marker,
                        compose, marker_pixels, write_mask)

EXIT_REFUSED = 2
DIRECTIONS = ("both", "x", "y")
DEFAULT_WIDTHS_MM = (0.200, 0.150, 0.100, 0.080, 0.060, 0.050)
DEFAULT_LINES = 5
DEFAULT_GRID_WIDTH_MM = 50.0
DEFAULT_GRID_HEIGHT_MM = 40.0
DEFAULT_GRID_LINE_MM = 0.2
DEFAULT_GRID_GAP_MM = 0.15
BLOCK_GAP_MM = 2.0
"""Physical gap between the horizontal and the vertical block of `bars`, in mm."""


# ──────────────────────────────── patterns ─────────────────────────────────

def _duplicates(widths_mm: tuple[float, ...], pixels: list[int], axis: str,
                groups: str) -> list[str]:
    """Requested widths that share one pixel count: identical groups on the plate."""
    by_pixels: dict[int, list[float]] = {}
    for width, count in zip(widths_mm, pixels):
        by_pixels.setdefault(count, []).append(width)
    said: list[str] = []
    for count in sorted(by_pixels):
        widths = by_pixels[count]
        if len(widths) > 1:
            listed = ", ".join(f"{width:.3f}" for width in widths)
            said.append(f"widths {listed} mm give the same {count} px along {axis}: "
                        f"the {groups} will be identical")
    return said


def build_bars(screen: Screen, widths_mm: tuple[float, ...], lines: int) -> Figure:
    """Groups of bars: horizontal block, then the vertical block to its right."""
    along_y = [_quantised(width, screen.pixel_y_mm, "Y", "the width", needed=True)
               for width in widths_mm]
    along_x = [_quantised(width, screen.pixel_x_mm, "X", "the width", needed=True)
               for width in widths_mm]

    group_gap_y = max(along_y)          # one largest width, as the plan asks
    group_gap_x = max(along_x)
    heights = [(2 * lines - 1) * count for count in along_y]
    block_height = sum(heights) + (len(widths_mm) - 1) * group_gap_y
    # The block is SQUARE in millimetres: its width in X pixels follows from its height.
    block_width = _quantised(screen.mm_y(block_height), screen.pixel_x_mm, "X",
                             "the horizontal block")
    widths_v = [(2 * lines - 1) * count for count in along_x]
    stack_width = sum(widths_v) + (len(widths_mm) - 1) * group_gap_x
    block_v_height = _quantised(screen.mm_x(stack_width), screen.pixel_y_mm, "Y",
                               "the vertical block")
    block_gap = _quantised(BLOCK_GAP_MM, screen.pixel_x_mm, "X", "the block gap")

    figure = np.zeros((max(block_height, block_v_height),
                       block_width + block_gap + stack_width), dtype=np.uint8)
    y = 0
    for index, height in enumerate(heights):
        for line in range(lines):
            top = y + line * 2 * along_y[index]
            figure[top:top + along_y[index], 0:block_width] = WHITE
        y += height + group_gap_y
    x = block_width + block_gap
    for index, width in enumerate(widths_v):
        for line in range(lines):
            left = x + line * 2 * along_x[index]
            figure[0:block_v_height, left:left + along_x[index]] = WHITE
        x += width + group_gap_x

    report = [
        f"pattern: bars, {lines} lines per group, gap = line width, groups in the order given",
        "layout: horizontal block at the left (lines along X: the Y resolution), vertical "
        f"block to its right (lines along Y: the X resolution), {BLOCK_GAP_MM:.3f} mm between them",
        "each block is square in millimetres, so a group measures the same span both ways",
        "widths (requested -> actual):",
        *(f"  {width:.3f} mm -> {_actual(along_y[index], screen.pixel_y_mm, 'Y')}, "
          f"{_actual(along_x[index], screen.pixel_x_mm, 'X')}"
          for index, width in enumerate(widths_mm)),
    ]
    # A warning is part of the report: a mask that lies about its own numbers is worse
    # than no mask, and the file must say so a month later too.
    warnings = (_duplicates(widths_mm, along_y, "Y", "horizontal groups")
                + _duplicates(widths_mm, along_x, "X", "vertical groups"))
    return Figure(pixels=figure,
                  report=tuple(report) + tuple(f"warning: {text}" for text in warnings))


def build_grid(screen: Screen, width_mm: float, height_mm: float, line_mm: float,
               gap_mm: float, direction: str) -> Figure:
    """A rectangle filled with lines: first line flush, whole lines only, rest is a gap."""
    along_x = direction in ("both", "x")     # lines along X, stacked in Y
    along_y = direction in ("both", "y")     # lines along Y, stacked in X
    rect_x = _quantised(width_mm, screen.pixel_x_mm, "X", "the rectangle width")
    rect_y = _quantised(height_mm, screen.pixel_y_mm, "Y", "the rectangle height")
    line_x = _quantised(line_mm, screen.pixel_x_mm, "X", "the line", needed=along_y)
    gap_x = _quantised(gap_mm, screen.pixel_x_mm, "X", "the gap", needed=along_y)
    line_y = _quantised(line_mm, screen.pixel_y_mm, "Y", "the line", needed=along_x)
    gap_y = _quantised(gap_mm, screen.pixel_y_mm, "Y", "the gap", needed=along_x)

    def fit(extent: int, line: int, gap: int) -> int:
        """Whole lines only: line ``i`` sits at ``i·(line + gap)``, flush with the edge."""
        if line < 1 or extent < line:
            return 0
        return (extent - line) // (line + gap) + 1

    count_x = fit(rect_y, line_y, gap_y) if along_x else 0
    count_y = fit(rect_x, line_x, gap_x) if along_y else 0
    if along_x and count_x == 0:
        raise Refusal(f"the {screen.mm_y(rect_y):.3f} mm side holds no line "
                      f"{line_mm:.3f} mm tall ({line_y} px of {rect_y} px)")
    if along_y and count_y == 0:
        raise Refusal(f"the {screen.mm_x(rect_x):.3f} mm side holds no line "
                      f"{line_mm:.3f} mm wide ({line_x} px of {rect_x} px)")

    figure = np.zeros((rect_y, rect_x), dtype=np.uint8)
    for index in range(count_x):
        top = index * (line_y + gap_y)
        figure[top:top + line_y, 0:rect_x] = WHITE
    for index in range(count_y):
        left = index * (line_x + gap_x)
        figure[0:rect_y, left:left + line_x] = WHITE

    report = [
        f"pattern: grid {width_mm:.3f} x {height_mm:.3f} mm, line {line_mm:.3f} mm, "
        f"gap {gap_mm:.3f} mm, direction {direction}",
        f"rectangle (actual): {rect_x} x {rect_y} px = "
        f"{screen.mm_x(rect_x):.3f} x {screen.mm_y(rect_y):.3f} mm",
        f"line: {line_mm:.3f} mm -> {_actual(line_x, screen.pixel_x_mm, 'X', needed=along_y)}, "
        f"{_actual(line_y, screen.pixel_y_mm, 'Y', needed=along_x)}",
        f"gap: {gap_mm:.3f} mm -> {_actual(gap_x, screen.pixel_x_mm, 'X', needed=along_y)}, "
        f"{_actual(gap_y, screen.pixel_y_mm, 'Y', needed=along_x)}",
        f"lines: along X {count_x}, along Y {count_y}",
        "the first line is flush with the near edge, a line is drawn only whole, the "
        "leftover at the far edge is a gap",
    ]
    return Figure(pixels=figure, report=tuple(report))


# ─────────────────────────────── command line ──────────────────────────────

def _pixels_pair(text: str) -> tuple[int, int]:
    """argparse type: ``WxH`` in whole pixels. No invented default (see the docstring)."""
    parts = text.replace(" ", "").lower().split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise argparse.ArgumentTypeError(
            f"expected two whole pixel counts separated by x, got {text!r}")
    width, height = (int(part) for part in parts)
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError(f"the screen must be at least one pixel, got {text!r}")
    return width, height


def _micrometres_pair(text: str) -> tuple[float, float]:
    """argparse type: ``PXxPY`` in micrometres — a pixel may be not square."""
    parts = text.replace(" ", "").lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"expected two pixel sizes separated by x, got {text!r}")
    try:
        x_um, y_um = (float(part) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected two pixel sizes separated by x, got {text!r}") from None
    if not (math.isfinite(x_um) and math.isfinite(y_um)) or x_um <= 0.0 or y_um <= 0.0:
        raise argparse.ArgumentTypeError(
            f"expected two positive pixel sizes in um, got {text!r}")
    return x_um, y_um


def _millimetres_pair(text: str) -> tuple[float, float]:
    """argparse type: ``X,Y`` in millimetres from the top-left pixel of the PNG."""
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected two millimetres separated by a comma, got {text!r}")
    try:
        x_mm, y_mm = (float(part) for part in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected two millimetres separated by a comma, got {text!r}") from None
    if not (math.isfinite(x_mm) and math.isfinite(y_mm)):
        raise argparse.ArgumentTypeError(
            f"expected two finite millimetres separated by a comma, got {text!r}")
    return x_mm, y_mm


def _width_list(text: str) -> tuple[float, ...]:
    """argparse type: comma separated positive widths, kept in the order given."""
    values: list[float] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            value = float(item)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a number, got {item!r}") from None
        if not math.isfinite(value) or value <= 0.0:
            raise argparse.ArgumentTypeError(f"expected a positive width in mm, got {item!r}")
        values.append(value)
    if not values:
        raise argparse.ArgumentTypeError("at least one width is required")
    return tuple(values)


def _line_count(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"a group needs at least one line, got {value}")
    return value


def _positive_mm(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {text!r}") from None
    if not math.isfinite(value) or value <= 0.0:
        raise argparse.ArgumentTypeError(f"expected a positive size in mm, got {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="msla_tests.py",
        description="Draw test masks for an MSLA printer in exact screen pixels.")
    kinds = parser.add_subparsers(dest="kind", metavar="PATTERN")
    kinds.required = True

    def common(kind: argparse.ArgumentParser) -> None:
        kind.add_argument("--screen-px", type=_pixels_pair, required=True,
                          help="screen size in pixels, WxH (no default: read the spec)")
        kind.add_argument("--pixel-um", type=_micrometres_pair, required=True,
                          help="one pixel in micrometres, PXxPY (a pixel may be not square)")
        kind.add_argument("-o", "--output", required=True, help="output PNG")
        kind.add_argument("--at", type=_millimetres_pair, default=None,
                          help="top-left corner of the figure, mm from the top-left pixel "
                               "(default: centred)")
        kind.add_argument("--mirror", action="store_true",
                          help="reflect the whole picture along X (a resist facing the screen)")
        kind.add_argument("--invert", action="store_true",
                          help="white = the gaps instead of the lines")
        kind.add_argument("--marker", action=argparse.BooleanOptionalAction, default=True,
                          help="draw the letter F in the top-left corner of the figure, so a "
                               "mirrored or upside-down exposure is visible on the plate "
                               "(--no-marker turns it off)")

    bars_parser = kinds.add_parser(
        "bars", help="lines and gaps of known widths, horizontal and vertical blocks",
        description="lines and gaps of known widths: horizontal block, vertical block to its right")
    common(bars_parser)
    bars_parser.add_argument("--widths-mm", type=_width_list, default=DEFAULT_WIDTHS_MM,
                             help="bar widths in mm, in the order to draw them")
    bars_parser.add_argument("--lines", type=_line_count, default=DEFAULT_LINES,
                             help="lines per group")
    bars_parser.set_defaults(handler=_cmd_bars)

    grid_parser = kinds.add_parser(
        "grid", help="a rectangle filled with lines",
        description="a rectangle filled with lines: the first line is flush with the near edge")
    common(grid_parser)
    grid_parser.add_argument("--width-mm", type=_positive_mm, default=DEFAULT_GRID_WIDTH_MM,
                             help="width of the rectangle in mm")
    grid_parser.add_argument("--height-mm", type=_positive_mm, default=DEFAULT_GRID_HEIGHT_MM,
                             help="height of the rectangle in mm")
    grid_parser.add_argument("--line-mm", type=_positive_mm, default=DEFAULT_GRID_LINE_MM,
                             help="line width in mm")
    grid_parser.add_argument("--gap-mm", type=_positive_mm, default=DEFAULT_GRID_GAP_MM,
                             help="gap between the lines in mm")
    grid_parser.add_argument("--direction", choices=DIRECTIONS, default="both",
                             help="lines along X, along Y, or both")
    grid_parser.set_defaults(handler=_cmd_grid)
    return parser


def _screen(args: argparse.Namespace) -> Screen:
    width_px, height_px = args.screen_px
    pixel_x_um, pixel_y_um = args.pixel_um
    return Screen(width_px=width_px, height_px=height_px,
                  pixel_x_um=pixel_x_um, pixel_y_um=pixel_y_um)


def _cmd_bars(args: argparse.Namespace, screen: Screen) -> Figure:
    return build_bars(screen, args.widths_mm, args.lines)


def _cmd_grid(args: argparse.Namespace, screen: Screen) -> Figure:
    return build_grid(screen, args.width_mm, args.height_mm, args.line_mm,
                      args.gap_mm, args.direction)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        screen = _screen(args)
        figure = args.handler(args, screen)
        if args.marker:
            figure = add_marker(screen, figure)
        image, placed = compose(screen, figure, args.at,
                                mirror=args.mirror, invert=args.invert)
        target = write_mask(args.output, screen, figure, image, placed)
    except Refusal as refusal:
        print(str(refusal), file=sys.stderr)
        return EXIT_REFUSED
    for line in figure.report:
        if line.startswith("warning: "):
            print(line, file=sys.stderr)
    print("\n".join(line for line in figure.report if not line.startswith("warning: ")))
    print("\n".join(placed))
    print(f"Wrote {target}: {screen.width_px} x {screen.height_px} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
