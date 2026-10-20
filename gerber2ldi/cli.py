# gerber2ldi/cli.py
"""Command line entry point: ``gerber2ldi <command> ...``.

Every command is a thin layer over the Qt-free core: parse arguments, call one
core function, print the result. No geometry, no units and no file formats are
decided here.

Expected refusals (`Gerber2LDIError`) are printed as ONE line and return a
non-zero code, with no traceback: a missing file is an answer, not a crash.
"""
import argparse
import math
import sys
from importlib.metadata import PackageNotFoundError, version

from .errors import Gerber2LDIError
from .i18n import _, setup_i18n
from .raster import iter_rows, write_tiff
from .source import load_layer

EXIT_OK = 0
EXIT_REFUSED = 2


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


def _version() -> str:
    try:
        return version("gerber2ldi")
    except PackageNotFoundError:
        return "unknown (not installed)"


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
