# gerber2ldi/cli.py
"""Command line entry point: ``gerber2ldi <command> ...``.

Every command is a thin layer over the Qt-free core: parse arguments, call one
core function, print the result. No geometry, no units and no file formats are
decided here.

Expected refusals (`Gerber2LDIError`) are printed as ONE line and return a
non-zero code, with no traceback: a missing file is an answer, not a crash.
Warnings are lines on stderr — never dialogs (rules: claude.md п.32).
"""
import argparse
import math
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .backends.gcode import read_segments, write_gcode
from .errors import Gerber2LDIError, JobError
from .i18n import _, setup_i18n
from .job import (PROCESSES, SIDES, SIDE_TOP, PROCESS_ETCH, build_job, format_duration,
                  ink_box, load_profile)
from .patterns import (DEFAULT_GRID_GAP_MM, DEFAULT_GRID_HEIGHT_MM, DEFAULT_GRID_LINE_MM,
                       DEFAULT_GRID_WIDTH_MM, DEFAULT_LENGTH_MM, DEFAULT_LINE_WIDTH_MM,
                       DEFAULT_SIDE_MM, DEFAULT_TILE_HEIGHT_MM, DEFAULT_TILE_WIDTH_MM,
                       DEFAULT_WIDTHS_MM, GRID_DIRECTIONS, Pattern, bars, dose,
                       fit_lines, grid, powers_from_series, square)
from .preview import write_preview
from .raster import iter_rows, write_tiff
from .source import load_layer

EXIT_OK = 0
EXIT_REFUSED = 2
PREVIEW_ROW_STEP_MM = 0.02


def _positive_mm(text: str) -> float:
    """argparse type: a finite, positive size in millimetres.

    Checked HERE, not in the core: a bad option is a usage error (argparse prints
    it and exits with 2), while a bad file is an expected refusal with our own
    message. Mixing the two would make `--step-y-mm 0` produce a traceback.
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            _("expected a number, got {value}").format(value=text)) from None
    if not math.isfinite(value) or value <= 0.0:
        raise argparse.ArgumentTypeError(
            _("expected a positive size in mm, got {value}").format(value=text))
    return value


def _place(text: str) -> tuple[float, float]:
    """argparse type: ``X,Y`` in millimetres, where the run-up start is placed."""
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            _("expected two millimetres separated by a comma, got {value}")
            .format(value=text))
    try:
        x_value, y_value = float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(
            _("expected a number, got {value}").format(value=text)) from None
    if not (math.isfinite(x_value) and math.isfinite(y_value)):
        raise argparse.ArgumentTypeError(
            _("expected two millimetres separated by a comma, got {value}")
            .format(value=text))
    return (x_value, y_value)


def _fraction(text: str) -> float:
    """argparse type: a power fraction in (0, 1]. The machine ceiling is checked
    later by the job, because it comes from the profile, not from the option."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            _("expected a number, got {value}").format(value=text)) from None
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise argparse.ArgumentTypeError(
            _("expected a fraction in (0, 1], got {value}").format(value=text))
    return value


def _widths(text: str) -> tuple[float, ...]:
    """argparse type: comma-separated positive widths, in the order given."""
    values = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(_positive_mm(item))
        except argparse.ArgumentTypeError as bad:
            raise argparse.ArgumentTypeError(
                _("bar widths: {error}").format(error=bad)) from None
    if not values:
        raise argparse.ArgumentTypeError(_("at least one bar width is required"))
    return tuple(values)


def _powers(text: str) -> tuple[float, ...]:
    """argparse type: comma-separated power fractions, each in (0, 1]."""
    values = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(_fraction(item))
        except argparse.ArgumentTypeError as bad:
            raise argparse.ArgumentTypeError(
                _("power levels: {error}").format(error=bad)) from None
    if not values:
        raise argparse.ArgumentTypeError(_("at least one power level is required"))
    return tuple(values)


def _count(text: str) -> int:
    """argparse type: how many dose levels, at least two (a series needs a step)."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            _("expected a whole number, got {value}").format(value=text)) from None
    if value < 2:
        raise argparse.ArgumentTypeError(
            _("a dose series needs at least two levels, got {value}").format(value=value))
    return value


def _version() -> str:
    try:
        return version("gerber2ldi")
    except PackageNotFoundError:
        return "unknown (not installed)"


def _print_warnings(lines) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def _print_passes(job) -> None:
    """The numbers the .nc header carries, in the operator's language.

    Only the WORDS are translated: the numbers come from ``Job.pass_lengths``, and the
    header keeps its own ASCII English copy because the file is written with
    ``encoding="ascii"`` (see `job.job`). The time is a LOWER bound — rapids, accelerations
    and M4/M5 are not in it.
    """
    exposed, full_width = job.pass_lengths()
    share = (exposed / full_width - 1.0) * 100.0 if full_width else 0.0
    print(_("passes: {exposed} mm exposed run (full-width would be {full} mm, {share}%), >= {time} at {speed} mm/s")
          .format(exposed=f"{exposed:.1f}", full=f"{full_width:.1f}",
                  share=f"{share:+.0f}",
                  time=format_duration(exposed / job.speed_mm_s),
                  speed=f"{job.speed_mm_s:g}"))


def _cmd_info(args: argparse.Namespace) -> int:
    """What is in the layer: units, size, apertures, polarity, shape count."""
    layer = load_layer(args.gerber)
    width, height = layer.size_mm
    print(_("File: {path}").format(path=args.gerber))
    print(_("Units in file: {unit}").format(unit=layer.unit))
    print(_("Size: {width:.3f} x {height:.3f} mm").format(width=width, height=height))
    print(_("Apertures: {count}").format(count=layer.aperture_count))
    print(_("Shapes: {count}").format(count=len(layer.shapes)))
    print(_("Clear polarity present: {yes_no}")
          .format(yes_no=_("yes") if layer.has_clear else _("no")))
    return EXIT_OK


def _cmd_raster(args: argparse.Namespace) -> int:
    """Turn the layer into machine rows and write the preview TIFF."""
    layer = load_layer(args.gerber)
    rows = list(iter_rows(layer.shapes, layer.bbox, args.step_y_mm))
    write_tiff(args.output, rows, layer.bbox, args.step_x_mm)
    inked = sum(1 for row in rows if not row.is_empty)
    print(_("Wrote {path}: {rows} rows, {filled} with copper")
          .format(path=args.output, rows=len(rows), filled=inked))
    return EXIT_OK


def _cmd_job(args: argparse.Namespace) -> int:
    """Build the job for the machine and write the G-code."""
    layer = load_layer(args.gerber)
    machine = load_profile(args.profile)
    frame = load_layer(args.frame) if args.frame else None
    job = build_job(layer, machine, side=args.side, process=args.process,
                    power=args.power, place_mm=args.place, frame=frame,
                    spot_compensation=not args.no_spot_compensation)
    _print_warnings(job.warnings)
    _print_passes(job)
    write_gcode(job, args.output)
    print(_("Wrote {path}: {rows} rows, {segments} segments, S{s}")
          .format(path=args.output, rows=job.inked_rows,
                  segments=job.segment_count, s=job.s_value))
    return EXIT_OK


def _cmd_preview(args: argparse.Namespace) -> int:
    """Draw what the machine will do: from the G-code, or from a layer + profile."""
    source = Path(args.input)
    if source.suffix.lower() in (".nc", ".gcode"):
        rows = read_segments(source)
        box = ink_box(rows)
    else:
        layer = load_layer(source)
        if args.profile:
            machine = load_profile(args.profile)
            power = args.power if args.power is not None else machine.laser.usable_fraction
            frame = load_layer(args.frame) if args.frame else None
            job = build_job(layer, machine, side=args.side, process=args.process,
                            power=power, place_mm=args.place, frame=frame,
                            spot_compensation=not args.no_spot_compensation)
            _print_warnings(job.warnings)
            rows, box = job.rows, job.box
        else:
            print(_("Preview without a profile: no calibration and no compensation applied"),
                  file=sys.stderr)
            rows = tuple(iter_rows(layer.shapes, layer.bbox, PREVIEW_ROW_STEP_MM))
            box = layer.bbox
    write_preview(args.output, rows, box)
    print(_("Wrote {path}").format(path=args.output))
    return EXIT_OK


def _dose_levels(args: argparse.Namespace) -> tuple[float, ...]:
    """The dose levels: ``--powers`` OR a geometric ``--from/--to/--count`` series.

    Exactly one form; neither is a refusal, because a strip without levels would
    silently expose the whole board at one power.
    """
    series = (args.start, args.stop, args.count)
    if args.powers is not None and any(value is not None for value in series):
        raise JobError(_("Use either --powers or --from/--to/--count, not both"))
    if args.powers is not None:
        return args.powers
    if all(value is None for value in series):
        raise JobError(
            _("A dose strip needs its levels: --powers 0.02,0.03,... or --from, --to and --count"))
    if any(value is None for value in series):
        raise JobError(_("--from, --to and --count go together: one of them is missing"))
    return powers_from_series(args.start, args.stop, args.count)


def _pattern_report(args: argparse.Namespace, pattern: Pattern, job) -> list[str]:
    """What the operator needs to read the board and update the calibration.

    The formulas come from the pattern verbatim (they are the SAME lines as in the
    ``.nc`` header, with the machine's numbers already substituted): translating them
    twice would let the copy on stdout and the copy on the machine drift apart.
    """
    if args.kind == "bars":
        widths = "  ".join(f"{width:.3f}" for width in args.widths)
        return [
            _("Bars: widths in fixed order {widths} mm, gap = width, five lines per group")
            .format(widths=widths),
            _("Layout: horizontal groups at the bottom left (along X: Y resolution), vertical groups to their right (along Y: X resolution)"),
        ]
    if args.kind == "dose":
        lines = [_("Dose strip: {count} tiles bottom to top, each with its own power")
                 .format(count=len(job.doses))]
        for index, level in enumerate(job.doses, start=1):
            lines.append(_("  {index}: y {y0} … {y1} mm, power {power}, S {s}")
                         .format(index=index, y0=round(level.y0_mm, 3),
                                 y1=round(level.y1_mm, 3),
                                 power=round(level.power, 4), s=level.s_value))
        return lines
    if args.kind == "grid":
        return [
            _("Grid {width} x {height} mm: line {line} mm, gap {gap} mm, period {period} mm")
            .format(width=args.width_mm, height=args.height_mm, line=args.line_mm,
                    gap=args.gap_mm, period=round(args.line_mm + args.gap_mm, 4)),
            _("Lines along X: {along_x}, along Y: {along_y}, direction {direction}")
            .format(along_x=(fit_lines(args.height_mm, args.line_mm, args.gap_mm)
                             if args.direction in ("both", "x") else 0),
                    along_y=(fit_lines(args.width_mm, args.line_mm, args.gap_mm)
                             if args.direction in ("both", "y") else 0),
                    direction=args.direction),
            _("Spot compensation: {state}")
            .format(state=_("on") if args.with_compensation else _("off")),
            _("The first line is flush with the near edge and the leftover at the far edge is a gap; the width of a line along X quantises to the row step"),
        ]
    return [
        _("Square {side} mm, line {width} mm, calibration {state}")
        .format(side=args.side_mm, width=args.line_width_mm,
                state=_("applied") if args.with_calibration else _("not applied")),
        _("Diagonals: d1 front-left to back-right, d2 front-right to back-left; theta = (d1 - d2) / (sqrt(2) * side)"),
        _("How the measurement becomes new calibration values (the scales multiply, the angle adds):"),
        *pattern.formulas,
        _("Write these numbers into the calibration file"),
    ]


def _cmd_pattern(args: argparse.Namespace) -> int:
    """Build a calibration pattern and write its program."""
    if args.kind is None:
        raise JobError(_("Choose a pattern: bars, dose or square"))
    machine = load_profile(args.profile)
    try:
        built: Pattern
        if args.kind == "bars":
            built = bars(machine, widths=args.widths, length_mm=args.length_mm)
        elif args.kind == "dose":
            built = dose(machine, _dose_levels(args), tile_width_mm=args.tile_width_mm,
                         tile_height_mm=args.tile_height_mm)
        elif args.kind == "grid":
            built = grid(machine, width_mm=args.width_mm, height_mm=args.height_mm,
                         line_mm=args.line_mm, gap_mm=args.gap_mm,
                         direction=args.direction)
        else:
            built = square(machine, side_mm=args.side_mm,
                           line_width_mm=args.line_width_mm,
                           with_calibration=args.with_calibration)
        job = build_job(built.layer, built.machine, power=getattr(args, "power", None),
                        power_bands=built.bands, notes=built.notes,
                        board=False,  # a pattern is not a board: no second side, no --frame
                        spot_compensation=args.with_compensation,
                        place_mm=args.place)
    except ValueError as bad:
        raise JobError(_("Cannot build the pattern: {error}").format(error=bad)) from bad
    _print_warnings(built.warnings + job.warnings)
    _print_passes(job)
    write_gcode(job, args.output)
    for line in _pattern_report(args, built, job):
        print(line)
    print(_("Wrote {path}: {rows} rows, {segments} segments")
          .format(path=args.output, rows=job.inked_rows, segments=job.segment_count))
    return EXIT_OK


def _add_job_options(parser: argparse.ArgumentParser, *, power_required: bool) -> None:
    parser.add_argument("--profile", required=True, help=_("machine profile (TOML)"))
    parser.add_argument("--side", choices=SIDES, default=SIDE_TOP,
                        help=_("side of the board"))
    parser.add_argument("--process", choices=PROCESSES, default=PROCESS_ETCH,
                        help=_("process: what the resist does"))
    parser.add_argument("--power", type=_fraction,
                        required=power_required, default=None,
                        help=_("laser power as a fraction of the module"))
    parser.add_argument("--place", type=_place, default=(0.0, 0.0),
                        help=_("where to put the run-up start, mm as X,Y"))
    parser.add_argument("--no-spot-compensation", action="store_true",
                        help=_("work without a measured spot radius"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gerber2ldi",
        description=_("Laser direct imaging of photoresist from Gerber files."))
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_version()}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    info = subparsers.add_parser(
        "info", help=_("show what is in a Gerber layer"),
        description=_("show what is in a Gerber layer"))
    info.add_argument("gerber", help=_("Gerber file"))
    info.set_defaults(handler=_cmd_info)

    raster = subparsers.add_parser(
        "raster", help=_("turn a Gerber layer into rows and a preview TIFF"),
        description=_("turn a Gerber layer into rows and a preview TIFF"))
    raster.add_argument("gerber", help=_("Gerber file"))
    raster.add_argument("-o", "--output", required=True, help=_("output TIFF"))
    raster.add_argument("--step-x-mm", type=_positive_mm, default=0.01,
                        help=_("preview pixel size across, mm"))
    raster.add_argument("--step-y-mm", type=_positive_mm, default=0.02,
                        help=_("machine row step, mm"))
    raster.set_defaults(handler=_cmd_raster)

    job = subparsers.add_parser(
        "job", help=_("make a machine job"),
        description=_("make a machine job"))
    job.add_argument("gerber", help=_("Gerber file"))
    _add_job_options(job, power_required=True)
    job.add_argument("--frame", default=None,
                     help=_("board outline (Edge_Cuts) shared by both sides"))
    job.add_argument("-o", "--output", required=True, help=_("output G-code"))
    job.set_defaults(handler=_cmd_job)

    pattern = subparsers.add_parser(
        "pattern", help=_("write a calibration program"),
        description=_("write a calibration program: test bars, dose strip, skew square, grid"))
    pattern.set_defaults(handler=_cmd_pattern, kind=None)
    kinds = pattern.add_subparsers(dest="kind", metavar="KIND")

    bars_parser = kinds.add_parser(
        "bars", help=_("test bars: lines and gaps of known widths"),
        description=_("test bars: lines and gaps of known widths, horizontal then vertical"))
    bars_parser.add_argument("--profile", required=True, help=_("machine profile (TOML)"))
    bars_parser.add_argument("-o", "--output", required=True, help=_("output G-code"))
    bars_parser.add_argument("--power", type=_fraction, required=True,
                             help=_("laser power as a fraction of the module"))
    bars_parser.add_argument("--widths", type=_widths, default=DEFAULT_WIDTHS_MM,
                             help=_("bar widths, mm, in the order to draw them"))
    bars_parser.add_argument("--length-mm", type=_positive_mm, default=DEFAULT_LENGTH_MM,
                             help=_("bar length, mm"))
    bars_parser.add_argument("--place", type=_place, default=(0.0, 0.0),
                             help=_("where to put the run-up start, mm as X,Y"))
    bars_parser.add_argument("--with-compensation", action="store_true",
                             help=_("apply spot compensation (off by default: the bars measure the spot)"))
    bars_parser.set_defaults(handler=_cmd_pattern)

    dose_parser = kinds.add_parser(
        "dose", help=_("dose strip: tiles stacked in Y, one power each"),
        description=_("dose strip: tiles stacked in Y, bottom to top with rising power"))
    dose_parser.add_argument("--profile", required=True, help=_("machine profile (TOML)"))
    dose_parser.add_argument("-o", "--output", required=True, help=_("output G-code"))
    dose_parser.add_argument("--powers", type=_powers, default=None,
                             help=_("power fractions, comma separated, bottom to top"))
    dose_parser.add_argument("--from", dest="start", type=_fraction, default=None,
                             help=_("lowest power fraction of a geometric series"))
    dose_parser.add_argument("--to", dest="stop", type=_fraction, default=None,
                             help=_("highest power fraction of the series"))
    dose_parser.add_argument("--count", type=_count, default=None,
                             help=_("how many levels the series has"))
    dose_parser.add_argument("--tile-width-mm", type=_positive_mm,
                             default=DEFAULT_TILE_WIDTH_MM, help=_("tile width, mm"))
    dose_parser.add_argument("--tile-height-mm", type=_positive_mm,
                             default=DEFAULT_TILE_HEIGHT_MM, help=_("tile height, mm"))
    dose_parser.add_argument("--place", type=_place, default=(0.0, 0.0),
                             help=_("where to put the run-up start, mm as X,Y"))
    dose_parser.add_argument("--with-compensation", action="store_true",
                             help=_("apply spot compensation (refused with per-row power)"))
    dose_parser.set_defaults(handler=_cmd_pattern)

    grid_parser = kinds.add_parser(
        "grid", help=_("grid: a rectangle filled with lines"),
        description=_("grid: fill a rectangle with lines — a coating test over the whole area"))
    grid_parser.add_argument("--profile", required=True, help=_("machine profile (TOML)"))
    grid_parser.add_argument("-o", "--output", required=True, help=_("output G-code"))
    grid_parser.add_argument("--power", type=_fraction, required=True,
                             help=_("laser power as a fraction of the module"))
    grid_parser.add_argument("--width-mm", type=_positive_mm,
                             default=DEFAULT_GRID_WIDTH_MM,
                             help=_("width of the rectangle, mm"))
    grid_parser.add_argument("--height-mm", type=_positive_mm,
                             default=DEFAULT_GRID_HEIGHT_MM,
                             help=_("height of the rectangle, mm"))
    grid_parser.add_argument("--line-mm", type=_positive_mm,
                             default=DEFAULT_GRID_LINE_MM, help=_("line width, mm"))
    grid_parser.add_argument("--gap-mm", type=_positive_mm,
                             default=DEFAULT_GRID_GAP_MM,
                             help=_("gap between the lines, mm"))
    grid_parser.add_argument("--direction", choices=GRID_DIRECTIONS, default="both",
                             help=_("lines along X, along Y, or both"))
    grid_parser.add_argument("--place", type=_place, default=(0.0, 0.0),
                             help=_("where to put the run-up start, mm as X,Y"))
    grid_parser.add_argument("--with-compensation", action="store_true",
                             help=_("apply spot compensation (off by default)"))
    grid_parser.set_defaults(handler=_cmd_pattern)

    square_parser = kinds.add_parser(
        "square", help=_("skew square: a contour L×L to measure the diagonals"),
        description=_("skew square: a contour L×L to measure the diagonals"))
    square_parser.add_argument("--profile", required=True, help=_("machine profile (TOML)"))
    square_parser.add_argument("-o", "--output", required=True, help=_("output G-code"))
    square_parser.add_argument("--power", type=_fraction, required=True,
                               help=_("laser power as a fraction of the module"))
    square_parser.add_argument("--side-mm", type=_positive_mm, default=DEFAULT_SIDE_MM,
                               help=_("square side, mm"))
    square_parser.add_argument("--line-width-mm", type=_positive_mm,
                               default=DEFAULT_LINE_WIDTH_MM, help=_("contour line width, mm"))
    square_parser.add_argument("--with-calibration", action="store_true",
                               help=_("draw it through the profile's calibration: the result is a residual"))
    square_parser.add_argument("--place", type=_place, default=(0.0, 0.0),
                               help=_("where to put the run-up start, mm as X,Y"))
    square_parser.add_argument("--with-compensation", action="store_true",
                               help=_("apply spot compensation (off by default)"))
    square_parser.set_defaults(handler=_cmd_pattern)

    preview = subparsers.add_parser(
        "preview", help=_("draw what the machine will do"),
        description=_("draw what the machine will do"))
    preview.add_argument("input", help=_("input Gerber or G-code"))
    preview.add_argument("-o", "--output", required=True, help=_("output PNG"))
    preview.add_argument("--profile", default=None, help=_("machine profile (TOML)"))
    preview.add_argument("--frame", default=None,
                         help=_("board outline (Edge_Cuts) shared by both sides"))
    preview.add_argument("--side", choices=SIDES, default=SIDE_TOP,
                         help=_("side of the board"))
    preview.add_argument("--process", choices=PROCESSES, default=PROCESS_ETCH,
                         help=_("process: what the resist does"))
    preview.add_argument("--power", type=_fraction, default=None,
                         help=_("laser power as a fraction of the module"))
    preview.add_argument("--place", type=_place, default=(0.0, 0.0),
                         help=_("where to put the run-up start, mm as X,Y"))
    preview.add_argument("--no-spot-compensation", action="store_true",
                         help=_("work without a measured spot radius"))
    preview.set_defaults(handler=_cmd_preview)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_i18n()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    try:
        return args.handler(args)
    except Gerber2LDIError as refusal:
        # One line, no traceback — the contract for an expected refusal.
        print(str(refusal), file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
