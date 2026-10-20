# gerber2ldi/job/profile.py
"""The machine profile and its calibration, read-only (plan Ш3, design §13.1, §17).

Two files, two meanings:

  ``profiles/<name>.toml``             — the TYPE of machine, a ready-made solution
                                         other people can reuse, in git;
  ``profiles/<name>.calibration.toml`` — THIS instance, measured on my machine,
                                         with the date of the measurement.

The split is deliberate (Денис 20.10): a profile travels by Syncthing and gets
shared; a calibration belongs to one piece of hardware and changes whenever the
mechanics are touched. **No port** lives here either — the port is per machine
and is a command-line argument.

Units: the file speaks micrometres for the small lengths (``row_step_um``,
``spot_radius_um``, suffix ``_um`` — plan §2.2), because that is how they get
measured; everything leaving this module is MILLIMETRES as float, like the rest
of the core.

A missing calibration file is NOT a refusal: the machine works without it, scale 1
and skew 0, with a warning string. A missing spot radius blocks only the spot
compensation, and the caller says so explicitly (Ш4: ``--no-spot-compensation``) —
compensating on an invented radius is worse than not compensating at all.
"""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..errors import ProfileError
from ..i18n import _

UM_PER_MM = 1000.0
FIRMWARE_DIALECTS = ("grbl", "grblhal", "fluidnc")
MAX_SKEW_DEG = 5.0
"""Beyond this the machine is broken, not skewed. The guard exists to catch a
degrees/radians mix-up: 0.2 rad written into ``skew_deg`` reads as 11.5°."""

ORIGINS = ("front-left", "front-right", "back-left", "back-right")
SCAN_AXES = ("x", "y")


@dataclass(frozen=True, slots=True)
class Table:
    size_x_mm: float
    size_y_mm: float
    origin: str


@dataclass(frozen=True, slots=True)
class Laser:
    wavelength_nm: float
    power_w: float
    usable_fraction: float
    s_max: float
    pwm_hz: float


@dataclass(frozen=True, slots=True)
class Scan:
    scan_axis: str
    row_step_mm: float
    speed_mm_s: float
    overscan_mm: float
    bidirectional: bool


@dataclass(frozen=True, slots=True)
class Calibration:
    """Measured on THIS machine; the defaults mean "not measured"."""

    scale_x: float = 1.0
    scale_y: float = 1.0
    skew_deg: float = 0.0
    spot_radius_mm: float | None = None
    measured: date | None = None


@dataclass(frozen=True, slots=True)
class Machine:
    name: str
    firmware: str
    table: Table
    laser: Laser
    scan: Scan
    calibration: Calibration
    source: Path
    warnings: tuple[str, ...] = field(default=())

    def require_spot_radius(self) -> float:
        """The measured spot radius, or a refusal that says where to put it."""
        if self.calibration.spot_radius_mm is None:
            raise ProfileError(
                _("Spot radius not measured in {path}; compensation needs a measurement (use --no-spot-compensation to work without it)")
                .format(path=self.source.with_name(
                    self.source.stem + ".calibration.toml")))
        return self.calibration.spot_radius_mm


# ───────────────────────────── reading helpers ──────────────────────────────

def _fail(path: Path, field_name: str, expectation: str, value) -> None:
    raise ProfileError(_("Profile {path}: {field} must be {expectation}, got {value}")
                       .format(path=path, field=field_name, expectation=expectation,
                               value=value))


def _section(document: dict, name: str, path: Path) -> dict:
    section = document.get(name)
    if not isinstance(section, dict):
        raise ProfileError(_("Profile {path}: [{section}] section is required")
                           .format(path=path, section=name))
    return section


def _required(section: dict, key: str, path: Path, section_name: str) -> object:
    if key not in section:
        raise ProfileError(_("Profile {path}: {field} is required")
                           .format(path=path, field=f"{section_name}.{key}"))
    return section[key]


def _number(section: dict, key: str, path: Path, section_name: str, *,
            positive: bool = False, non_negative: bool = False) -> float:
    value = _required(section, key, path, section_name)
    field_name = f"{section_name}.{key}"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, field_name, "a number", value)
    number = float(value)
    if not math.isfinite(number):
        _fail(path, field_name, "finite", value)
    if positive and number <= 0.0:
        _fail(path, field_name, "positive", value)
    if non_negative and number < 0.0:
        _fail(path, field_name, "non-negative", value)
    return number


def _choice(section: dict, key: str, path: Path, section_name: str,
            allowed: tuple[str, ...]) -> str:
    value = _required(section, key, path, section_name)
    if not isinstance(value, str) or value.lower() not in allowed:
        _fail(path, f"{section_name}.{key}", f"one of {', '.join(allowed)}", value)
    return value.lower()


def _boolean(section: dict, key: str, path: Path, section_name: str) -> bool:
    value = _required(section, key, path, section_name)
    if not isinstance(value, bool):
        _fail(path, f"{section_name}.{key}", "true or false", value)
    return value


def _read_toml(path: Path, kind: str) -> dict:
    if not path.is_file():
        raise ProfileError(_("Profile file not found: {path}").format(path=path))
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        message = (_("Cannot read profile {path}: {error}") if kind == "profile"
                   else _("Cannot read calibration {path}: {error}"))
        raise ProfileError(message.format(path=path, error=exc)) from exc


# ─────────────────────────────── public API ────────────────────────────────

def calibration_path(profile_path: str | Path) -> Path:
    """``profiles/china.toml`` → ``profiles/china.calibration.toml``."""
    profile = Path(profile_path)
    return profile.with_name(profile.stem + ".calibration.toml")


def load_calibration(path: str | Path, *, required: bool = False) -> Calibration:
    """Read the calibration file. Missing and not required → the neutral defaults."""
    source = Path(path)
    if not source.is_file():
        if required:
            raise ProfileError(_("Profile file not found: {path}").format(path=source))
        return Calibration()
    document = _read_toml(source, "calibration")
    section = document.get("calibration", {})
    if not isinstance(section, dict):
        raise ProfileError(_("Profile {path}: [calibration] section is required")
                           .format(path=source))

    def number(key: str, *, positive: bool) -> float:
        if key not in section:
            return 1.0
        value = section[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(source, f"calibration.{key}", "a number", value)
        number_value = float(value)
        if not math.isfinite(number_value) or (positive and number_value <= 0.0):
            _fail(source, f"calibration.{key}", "positive", value)
        return number_value

    scale_x = number("scale_x", positive=True)
    scale_y = number("scale_y", positive=True)

    skew_deg = 0.0
    if "skew_deg" in section:
        raw = section["skew_deg"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail(source, "calibration.skew_deg", "a number", raw)
        skew_deg = float(raw)
        if not math.isfinite(skew_deg) or abs(skew_deg) > MAX_SKEW_DEG:
            _fail(source, "calibration.skew_deg",
                  f"a finite angle within ±{MAX_SKEW_DEG:g} degrees", raw)

    spot_radius_mm: float | None = None
    raw_spot = section.get("spot_radius_um")
    if raw_spot is not None:
        if isinstance(raw_spot, bool) or not isinstance(raw_spot, (int, float)):
            _fail(source, "calibration.spot_radius_um", "a number", raw_spot)
        if float(raw_spot) > 0.0:  # 0 means "not measured yet" (template value)
            spot_radius_mm = float(raw_spot) / UM_PER_MM

    measured: date | None = None
    raw_date = section.get("measured")
    if isinstance(raw_date, date):
        measured = raw_date
    elif isinstance(raw_date, str):
        try:
            measured = date.fromisoformat(raw_date)
        except ValueError:
            _fail(source, "calibration.measured", "an ISO date (YYYY-MM-DD)", raw_date)
    elif raw_date is not None:
        _fail(source, "calibration.measured", "an ISO date (YYYY-MM-DD)", raw_date)

    return Calibration(scale_x=scale_x, scale_y=scale_y, skew_deg=skew_deg,
                       spot_radius_mm=spot_radius_mm, measured=measured)


def load_profile(path: str | Path) -> Machine:
    """Read a machine profile and the calibration file sitting next to it."""
    source = Path(path)
    document = _read_toml(source, "profile")

    machine = _section(document, "machine", source)
    name = _required(machine, "name", source, "machine")
    if not isinstance(name, str) or not name.strip():
        _fail(source, "machine.name", "a non-empty string", name)
    firmware = _choice(machine, "firmware", source, "machine", FIRMWARE_DIALECTS)

    table = _section(document, "table", source)
    size_x = _number(table, "size_x_mm", source, "table", positive=True)
    size_y = _number(table, "size_y_mm", source, "table", positive=True)
    origin = _choice(table, "origin", source, "table", ORIGINS)

    laser = _section(document, "laser", source)
    wavelength = _number(laser, "wavelength_nm", source, "laser", positive=True)
    power_w = _number(laser, "power_w", source, "laser", non_negative=True)
    usable = _number(laser, "usable_fraction", source, "laser")
    if not 0.0 < usable <= 1.0:
        _fail(source, "laser.usable_fraction", "a fraction in (0, 1]", usable)
    s_max = _number(laser, "s_max", source, "laser", positive=True)
    pwm_hz = _number(laser, "pwm_hz", source, "laser", positive=True)

    scan = _section(document, "scan", source)
    axis = _choice(scan, "scan_axis", source, "scan", SCAN_AXES)
    row_step_mm = _number(scan, "row_step_um", source, "scan", positive=True) / UM_PER_MM
    speed = _number(scan, "speed_mm_s", source, "scan", positive=True)
    overscan = _number(scan, "overscan_mm", source, "scan", non_negative=True)
    bidirectional = _boolean(scan, "bidirectional", source, "scan")

    calibration_file = calibration_path(source)
    calibration = load_calibration(calibration_file)
    warnings: list[str] = []
    # Single-line literals on purpose: implicit concatenation inside `_()` is the
    # classic extraction trap (i18troubles) — the message would never be found.
    if not calibration_file.is_file():
        warnings.append(
            _("Machine is not calibrated: {path} not found; assuming scale 1 and skew 0")
            .format(path=calibration_file))
    if calibration.spot_radius_mm is None:
        warnings.append(
            _("Spot radius not measured in {path}; compensation needs a measurement (use --no-spot-compensation to work without it)")
            .format(path=calibration_file))

    return Machine(
        name=name,
        firmware=firmware,
        table=Table(size_x_mm=size_x, size_y_mm=size_y, origin=origin),
        laser=Laser(wavelength_nm=wavelength, power_w=power_w,
                    usable_fraction=usable, s_max=s_max, pwm_hz=pwm_hz),
        scan=Scan(scan_axis=axis, row_step_mm=row_step_mm, speed_mm_s=speed,
                  overscan_mm=overscan, bidirectional=bidirectional),
        calibration=calibration,
        source=source,
        warnings=tuple(warnings),
    )
