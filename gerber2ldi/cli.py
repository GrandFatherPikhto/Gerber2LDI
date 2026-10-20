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
from .errors import Gerber2LDIError
from .i18n import _, setup_i18n
from .job import PROCESSES, SIDES, SIDE_TOP, PROCESS_ETCH, build_job, ink_box, load_profile
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


def _version() -> str:
    try:
        return version("gerber2ldi")
    except PackageNotFoundError:
        return "unknown (not installed)"


def _print_warnings(lines) -> None:
    for line in lines:
        print(line, file=sys.stderr)


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
    job = build_job(layer, machine, side=args.side, process=args.process,
                    power=args.power, place_mm=args.place,
                    spot_compensation=not args.no_spot_compensation)
    _print_warnings(job.warnings)
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
            job = build_job(layer, machine, side=args.side, process=args.process,
                            power=power, place_mm=args.place,
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
    job.add_argument("-o", "--output", required=True, help=_("output G-code"))
    job.set_defaults(handler=_cmd_job)

    preview = subparsers.add_parser(
        "preview", help=_("draw what the machine will do"),
        description=_("draw what the machine will do"))
    preview.add_argument("input", help=_("input Gerber or G-code"))
    preview.add_argument("-o", "--output", required=True, help=_("output PNG"))
    preview.add_argument("--profile", default=None, help=_("machine profile (TOML)"))
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
