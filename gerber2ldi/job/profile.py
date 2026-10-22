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

THE MOTOR DECIDES THE ROW STEP. ``[machine] steps_per_mm_x/y`` are ``$100/$101``:
how many microsteps the controller counts per millimetre. A row step that is not a
whole number of those steps cannot be walked — 20 µm rows on a 12.5 µm step land
12.5 and 25 µm apart in turn and band the exposure (found by hand on 22.10, so it
is refused here with the numbers). The axis steps are a property of the MACHINE,
the row step is a property of the SCAN, hence the two sections.

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

SCALE_RANGE = (0.95, 1.05)
"""Allowed measured axis scale — the same kind of guard as MAX_SKEW_DEG above.

A belt stretch or a wrong microstep setting moves the real/commanded ratio by
tenths of a percent, so anything outside ±5 % is not a calibration: 100.2 means the
file holds PERCENT (or ppm) instead of a factor, 1.2 means a 20 % error that would
double the size of every feature once the sign of the mistake is right. Refusing
beats silently scaling a board by 100×.
"""

STEP_TOLERANCE = 1e-6
"""How far ``row_step_um`` may sit from a whole number of microsteps, in STEPS.

The file holds numbers, not measurements, so this only absorbs float noise
(25 µm · 80 step/mm / 1000 = 2.0000000000000004); a genuine half step is a refusal.
"""

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
class Motor:
    """Axis resolution as the firmware counts it (``$100``/``$101``).

    ``steps_per_mm`` is what the controller has been TOLD, not a measurement of the
    travel: the travel is ``calibration.scale_x/y``. This one answers a different
    question — which positions can be commanded at all.
    """

    steps_per_mm_x: float
    steps_per_mm_y: float

    @property
    def step_x_um(self) -> float:
        return UM_PER_MM / self.steps_per_mm_x

    @property
    def step_y_um(self) -> float:
        return UM_PER_MM / self.steps_per_mm_y

    @property
    def step_x_mm(self) -> float:
        return 1.0 / self.steps_per_mm_x

    @property
    def step_y_mm(self) -> float:
        return 1.0 / self.steps_per_mm_y


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
    motor: Motor
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


def _check_row_step(row_step_um: float, motor: Motor, path: Path) -> None:
    """The row step must be a whole number of microsteps, or the rows alternate apart.

    The trap of 22.10: 20 µm rows on a ``$101 = 80`` step/mm machine (12.5 µm per step)
    put consecutive rows 12.5 and 25 µm apart in turn, and the exposure came out banded.
    A stepper can only stop on its own microsteps, so a fractional step count is not a
    tuning choice — it is a different row grid from row to row.
    """
    steps = row_step_um * motor.steps_per_mm_y / UM_PER_MM
    if abs(steps - round(steps)) <= STEP_TOLERANCE:
        return
    low = math.floor(steps) * motor.step_y_um
    high = math.ceil(steps) * motor.step_y_um
    raise ProfileError(
        _("Profile {path}: scan.row_step_um = {row:g} um is not a whole number of motor steps along Y: machine.steps_per_mm_y = {per:g} step/mm gives a {step:g} um step, and {row:g} um is {count:g} of them — rows would land {low:g} and {high:g} um apart in turn")
        .format(path=path, row=row_step_um, per=motor.steps_per_mm_y,
                step=motor.step_y_um, count=round(steps, 3), low=low, high=high))


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

    def scale(key: str) -> float:
        """A measured axis factor; absent means 1.0, out of range is a refusal."""
        if key not in section:
            return 1.0
        value = section[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail(source, f"calibration.{key}", "a number", value)
        number_value = float(value)
        low, high = SCALE_RANGE
        if not math.isfinite(number_value):
            _fail(source, f"calibration.{key}", "finite", value)
        if not low <= number_value <= high:
            _fail(source, f"calibration.{key}",
                  f"a factor in [{low:g}, {high:g}]", value)
        return number_value

    scale_x = scale("scale_x")
    scale_y = scale("scale_y")

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
        spot_um = float(raw_spot)
        # TOML happily carries `nan` and `inf`, and a negative or non-finite radius
        # is a broken measurement, not "unmeasured": the old `> 0` test turned all
        # three into "not measured" and then silently built a job without
        # compensation. "Not measured" is 0 or an absent key, nothing else.
        if not math.isfinite(spot_um):
            _fail(source, "calibration.spot_radius_um", "finite", raw_spot)
        if spot_um < 0.0:
            _fail(source, "calibration.spot_radius_um", "non-negative", raw_spot)
        spot_radius_mm = spot_um / UM_PER_MM if spot_um > 0.0 else None

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
    # Mandatory: without the axis resolution the row step cannot be checked against
    # the microsteps, and a silently banded exposure looks like a resist problem.
    motor = Motor(
        steps_per_mm_x=_number(machine, "steps_per_mm_x", source, "machine", positive=True),
        steps_per_mm_y=_number(machine, "steps_per_mm_y", source, "machine", positive=True),
    )

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
    row_step_um = _number(scan, "row_step_um", source, "scan", positive=True)
    row_step_mm = row_step_um / UM_PER_MM
    speed = _number(scan, "speed_mm_s", source, "scan", positive=True)
    overscan = _number(scan, "overscan_mm", source, "scan", non_negative=True)
    bidirectional = _boolean(scan, "bidirectional", source, "scan")
    # Refused, not silently ignored: the job writer assumes X scanning in one
    # direction, and quietly building a program for a different machine is worse
    # than not building one.
    if axis != "x":
        _fail(source, "scan.scan_axis", "x (only X scanning is implemented)", axis)
    if bidirectional:
        _fail(source, "scan.bidirectional",
              "false (two-direction scanning is not implemented)", bidirectional)
    # The scan steps along Y, so the Y resolution is what the row step must fit.
    _check_row_step(row_step_um, motor, source)

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
        # Command-neutral on purpose: the refusal in `require_spot_radius` carries the
        # remedy (its flag), while this line only states a fact about the profile —
        # a calibration PATTERN runs without compensation by design and must not be
        # told to pass a flag it does not have.
        warnings.append(
            _("Spot radius not measured in {path}: compensation stays unavailable until it is measured")
            .format(path=calibration_file))

    return Machine(
        name=name,
        firmware=firmware,
        table=Table(size_x_mm=size_x, size_y_mm=size_y, origin=origin),
        laser=Laser(wavelength_nm=wavelength, power_w=power_w,
                    usable_fraction=usable, s_max=s_max, pwm_hz=pwm_hz),
        scan=Scan(scan_axis=axis, row_step_mm=row_step_mm, speed_mm_s=speed,
                  overscan_mm=overscan, bidirectional=bidirectional),
        motor=motor,
        calibration=calibration,
        source=source,
        warnings=tuple(warnings),
    )
