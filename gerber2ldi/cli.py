# gerber2ldi/cli.py
"""Command line entry point: ``gerber2ldi <command> ...``.

Every command is a thin layer over the Qt-free core; the subcommands of the
first step (info, raster, job, gcode, preview, pattern) are added here as the
core grows.
"""
import argparse
from importlib.metadata import PackageNotFoundError, version

from .i18n import _, setup_i18n


def _version() -> str:
    try:
        return version("gerber2ldi")
    except PackageNotFoundError:
        return "unknown (not installed)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gerber2ldi",
        description=_("Laser direct imaging of photoresist from Gerber files."))
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_version()}")
    parser.add_subparsers(dest="command", metavar="COMMAND")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_i18n()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
