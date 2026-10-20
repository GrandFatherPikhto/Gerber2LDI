# gerber2ldi/job/job.py
"""The job: machine rows with the whole calibration chain and the hard limits.

Chain (map.md, design §3.1/§17): raster → ``scale_x`` → spot compensation → axis
skew → mirror (bottom side) → polarity (process). ``scale_y`` is NOT here: it maps
a machine row height to a figure height, so it belongs to the raster call
(``iter_rows(..., y_scale=...)``) — otherwise compensation and skew would act on
different axes and the error would grow away from the origin.

Two things in this module REFUSE instead of adjusting:

  * power above ``usable_fraction`` — the module degrades and the resistor gets
    over-exposed, so a request above the ceiling is answered with a message, never
    with a silent clamp (design §13.1, Денис 20.10);
  * a figure that does not fit the table together with the run-up strip — the
    machine would hit a limit mid-pass.

A missing spot radius also refuses, unless compensation is switched off explicitly
(`--no-spot-compensation`): compensating on an invented radius is worse than not
compensating at all (plan Ш3).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import JobError
from ..i18n import _
from ..raster import iter_rows
from ..scan import ScanRow, apply_skew, compensate, invert, mirror, scale
from ..source import Layer
from .profile import Machine

PROCESS_ETCH = "etch-resist"
PROCESS_PLATING = "plating-resist"
PROCESSES = (PROCESS_ETCH, PROCESS_PLATING)

SIDE_TOP = "top"
SIDE_BOTTOM = "bottom"
SIDES = (SIDE_TOP, SIDE_BOTTOM)

Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Job:
    """Machine-space rows plus everything the G-code writer needs."""

    rows: tuple[ScanRow, ...]
    box: Box
    overscan_mm: float
    speed_mm_s: float
    s_value: int
    power_fraction: float
    side: str
    process: str
    warnings: tuple[str, ...] = ()

    @property
    def pass_start_x(self) -> float:
        """Where the run-up begins: the figure edge minus the strip."""
        return self.box[0] - self.overscan_mm

    @property
    def pass_end_x(self) -> float:
        return self.box[2] + self.overscan_mm

    @property
    def segment_count(self) -> int:
        return sum(len(row.segments) for row in self.rows)

    @property
    def inked_rows(self) -> int:
        return sum(1 for row in self.rows if not row.is_empty)


def _check_choice(value: str, allowed: tuple[str, ...], field_name: str) -> str:
    if value not in allowed:
        raise JobError(_("Unknown {field} {value} (one of {allowed})")
                       .format(field=field_name, value=value,
                               allowed=", ".join(allowed)))
    return value


def _s_value(power: float, machine: Machine) -> int:
    """Requested fraction → S, refusing above the ceiling (never clamping)."""
    ceiling = machine.laser.usable_fraction
    if not math.isfinite(power) or power <= 0.0:
        raise JobError(_("Requested power {requested} must be a fraction above zero")
                       .format(requested=power))
    if power > ceiling:
        raise JobError(
            _("Requested power {requested} is above the ceiling {ceiling} from {path}")
            .format(requested=power, ceiling=ceiling, path=machine.source))
    return int(round(power * machine.laser.s_max))


def ink_box(rows: tuple[ScanRow, ...]) -> Box:
    """Bounding box of what actually gets drawn, in machine millimetres."""
    xs0: list[float] = []
    xs1: list[float] = []
    ys: list[float] = []
    for row in rows:
        for start, end in row.segments:
            xs0.append(start)
            xs1.append(end)
            ys.append(row.y_mm)
    if not xs0:
        raise JobError(_("The job is empty: nothing to expose after the transforms"))
    return (min(xs0), min(ys), max(xs1), max(ys))


def build_job(layer: Layer, machine: Machine, *, side: str = SIDE_TOP,
              process: str = PROCESS_ETCH, power: float,
              spot_compensation: bool = True) -> Job:
    """Turn a layer into machine rows, applying the calibration and the limits."""
    _check_choice(side, SIDES, _("side"))
    _check_choice(process, PROCESSES, _("process"))
    s_value = _s_value(power, machine)

    calibration = machine.calibration
    warnings: list[str] = list(machine.warnings)

    radius: float | None = None
    if spot_compensation:
        radius = machine.require_spot_radius()
    else:
        warnings.append(_("Spot compensation disabled explicitly (--no-spot-compensation)"))

    x_scale, y_scale = calibration.scale_x, calibration.scale_y
    x0, y0, x1, y1 = layer.bbox
    machine_box = (x0 * x_scale, y0 * y_scale, x1 * x_scale, y1 * y_scale)

    rows = tuple(
        ScanRow(row.y_mm, scale(row.segments, x_scale))
        for row in iter_rows(layer.shapes, machine_box,
                             machine.scan.row_step_mm, y_scale=y_scale))

    if radius is not None:
        rows = tuple(compensate(rows, machine.scan.row_step_mm, radius))
    if calibration.skew_deg:
        rows = tuple(apply_skew(rows, math.tan(math.radians(calibration.skew_deg))))

    ink = ink_box(rows)
    if side == SIDE_BOTTOM:
        # Mirror about the CENTRE of the ink, so the figure stays where the
        # operator put it on the table.
        axis = (ink[0] + ink[2]) / 2.0
        rows = tuple(ScanRow(row.y_mm, mirror(row.segments, axis)) for row in rows)
        ink = ink_box(rows)
    if process == PROCESS_PLATING:
        # Expose the gaps instead of the copper, inside the figure's own X extent.
        rows = tuple(ScanRow(row.y_mm, invert(row.segments, ink[0], ink[2]))
                     for row in rows)

    overscan = machine.scan.overscan_mm
    width = ink[2] - ink[0] + 2.0 * overscan
    if width > machine.table.size_x_mm:
        raise JobError(
            _("Figure plus run-up needs {width} mm across, the table has {table} mm")
            .format(width=round(width, 3), table=machine.table.size_x_mm))
    height = ink[3] - ink[1]
    if height > machine.table.size_y_mm:
        raise JobError(
            _("Figure needs {height} mm along, the table has {table} mm")
            .format(height=round(height, 3), table=machine.table.size_y_mm))

    if radius is not None:
        period = machine.scan.speed_mm_s / machine.laser.pwm_hz
        if period > radius:
            warnings.append(
                _("PWM period {period} mm is wider than the spot radius {radius} mm: dashes, not a line")
                .format(period=round(period, 5), radius=round(radius, 5)))

    return Job(rows=rows, box=ink, overscan_mm=overscan,
               speed_mm_s=machine.scan.speed_mm_s, s_value=s_value,
               power_fraction=power, side=side, process=process,
               warnings=tuple(warnings))
