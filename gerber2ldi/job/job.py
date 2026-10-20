# gerber2ldi/job/job.py
"""The job: machine rows with the whole calibration chain and the hard limits.

ORDER OF THE CHAIN — it is not arbitrary (Денис 20.10, design §17):

  1. raster (with ``y_scale``: a machine row samples the figure at
     ``y_machine / scale_y``);
  2. mirror, if the bottom side is being exposed;
  3. polarity: what is exposed — copper, or the gaps between copper;
  4. spot compensation. It narrows WHAT IS EXPOSED, so it must come after the
     polarity flip; the other order makes the exposed gaps of ``plating-resist``
     WIDER by r instead of narrower;
  5. station calibration: ``scale_x`` and then the axis skew. Skew after the mirror
     keeps its sign the same in machine coordinates on both sides; the other order
     turns it into ``2·θ·L`` between the sides (design §17);
  6. placement on the table: the run-up start and the bottom edge of the figure are
     moved to ``place_mm``, so that machine coordinates start at the table origin
     and a KiCad board with negative coordinates becomes a valid program.

Refusals, never silent adjustments:

  * power above ``usable_fraction`` — the module degrades, the resist is
    over-exposed;
  * a pass that leaves the table after placement, with the numbers;
  * a spot radius that was never measured, unless ``--no-spot-compensation``;
  * a table origin the placement does not support yet (only ``front-left``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import JobError
from ..i18n import _
from ..raster import iter_rows
from ..scan import ScanRow, apply_skew, compensate, invert, mirror, scale, shift
from ..source import Layer
from .profile import Machine

PROCESS_ETCH = "etch-resist"
PROCESS_PLATING = "plating-resist"
PROCESSES = (PROCESS_ETCH, PROCESS_PLATING)

SIDE_TOP = "top"
SIDE_BOTTOM = "bottom"
SIDES = (SIDE_TOP, SIDE_BOTTOM)

SUPPORTED_ORIGIN = "front-left"

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
    place_mm: tuple[float, float] = (0.0, 0.0)
    warnings: tuple[str, ...] = ()

    @property
    def feed_mm_per_min(self) -> float:
        """GRBL's ``F`` is millimetres per MINUTE; the profile speaks mm/s.

        The conversion lives here and only here: writing ``F50`` for 50 mm/s runs
        the machine sixty times slower and deposits sixty times the dose.
        """
        return self.speed_mm_s * 60.0

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
    """Requested fraction → S, refusing above the ceiling (never clamping).

    ``floor`` on purpose: with a non-round ``s_max`` rounding up could land one
    step ABOVE ``usable_fraction · s_max``, and that ceiling is hard.
    """
    ceiling = machine.laser.usable_fraction
    if not math.isfinite(power) or power <= 0.0:
        raise JobError(_("Requested power {requested} must be a fraction above zero")
                       .format(requested=power))
    if power > ceiling:
        raise JobError(
            _("Requested power {requested} is above the ceiling {ceiling} from {path}")
            .format(requested=power, ceiling=ceiling, path=machine.source))
    return int(math.floor(power * machine.laser.s_max + 1e-9))


def ink_box(rows: tuple[ScanRow, ...]) -> Box:
    """Bounding box of what actually gets drawn, in the current coordinates."""
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


def _check_table(machine: Machine, box: Box, overscan: float) -> None:
    """The whole pass — run-up included — must sit inside the table."""
    start_x = box[0] - overscan
    end_x = box[2] + overscan
    if start_x < 0.0 or end_x > machine.table.size_x_mm:
        raise JobError(
            _("The pass runs {start} … {end} mm across, the table spans 0 … {table} mm")
            .format(start=round(start_x, 3), end=round(end_x, 3),
                    table=machine.table.size_x_mm))
    if box[1] < 0.0 or box[3] > machine.table.size_y_mm:
        raise JobError(
            _("The pass runs {start} … {end} mm along, the table spans 0 … {table} mm")
            .format(start=round(box[1], 3), end=round(box[3], 3),
                    table=machine.table.size_y_mm))


def build_job(layer: Layer, machine: Machine, *, side: str = SIDE_TOP,
              process: str = PROCESS_ETCH, power: float,
              spot_compensation: bool = True,
              place_mm: tuple[float, float] = (0.0, 0.0)) -> Job:
    """Turn a layer into machine rows, applying the calibration and the limits."""
    _check_choice(side, SIDES, _("side"))
    _check_choice(process, PROCESSES, _("process"))
    if machine.table.origin != SUPPORTED_ORIGIN:
        raise JobError(
            _("Table origin {origin} is not supported yet: only {supported}")
            .format(origin=machine.table.origin, supported=SUPPORTED_ORIGIN))
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
    rows = tuple(iter_rows(layer.shapes, machine_box,
                           machine.scan.row_step_mm, y_scale=y_scale))

    # 2. what will be exposed: mirror for the bottom side...
    if side == SIDE_BOTTOM:
        figure = ink_box(rows)
        axis = (figure[0] + figure[2]) / 2.0
        rows = tuple(ScanRow(row.y_mm, mirror(row.segments, axis)) for row in rows)
    # ...then the polarity: copper, or the gaps between it.
    figure = ink_box(rows)
    if process == PROCESS_PLATING:
        rows = tuple(ScanRow(row.y_mm, invert(row.segments, figure[0], figure[2]))
                     for row in rows)

    # 3. compensation narrows WHAT IS EXPOSED — hence after the polarity flip.
    if radius is not None:
        # The radius is measured in MACHINE millimetres and is applied here, before
        # scale_x. With the allowed ±5 % scale the radius is therefore off by at
        # most 5 % (7 µm on a 150 µm spot) — a documented assumption: the
        # measurement lives on the machine, the figure does not.
        rows = tuple(compensate(rows, machine.scan.row_step_mm, radius))

    # 4. station calibration: X scale, then skew (after the mirror, on purpose).
    rows = tuple(ScanRow(row.y_mm, scale(row.segments, x_scale)) for row in rows)
    if calibration.skew_deg:
        rows = tuple(apply_skew(rows, math.tan(math.radians(calibration.skew_deg))))

    # 5. placement: the run-up start and the bottom edge go to place_mm.
    overscan = machine.scan.overscan_mm
    ink = ink_box(rows)
    place_x, place_y = float(place_mm[0]), float(place_mm[1])
    shift_x = place_x - (ink[0] - overscan)
    shift_y = place_y - ink[1]
    rows = tuple(ScanRow(row.y_mm + shift_y, shift(row.segments, shift_x))
                 for row in rows)
    ink = (ink[0] + shift_x, ink[1] + shift_y, ink[2] + shift_x, ink[3] + shift_y)

    _check_table(machine, ink, overscan)

    if radius is not None:
        period = machine.scan.speed_mm_s / machine.laser.pwm_hz
        if period > radius:
            warnings.append(
                _("PWM period {period} mm is wider than the spot radius {radius} mm: dashes, not a line")
                .format(period=round(period, 5), radius=round(radius, 5)))

    return Job(rows=rows, box=ink, overscan_mm=overscan,
               speed_mm_s=machine.scan.speed_mm_s, s_value=s_value,
               power_fraction=power, side=side, process=process,
               place_mm=(place_x, place_y), warnings=tuple(warnings))
