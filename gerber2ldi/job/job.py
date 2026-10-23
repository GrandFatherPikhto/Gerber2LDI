# gerber2ldi/job/job.py
"""The job: machine rows with the whole calibration chain and the hard limits.

POWER PER ROW. A dose strip needs a different power on each band of rows, so the
job carries ``s_values`` — one ``S`` word per row — beside the uniform ``s_value``.
The band is a y-range of the FIGURE (``PowerBand``), and the row that samples it is
found during the raster, where the Y calibration already knows which figure height a
commanded row covers. Bands are refused together with spot compensation: that step
adds and removes rows, so a band would no longer own its rows.

HEADER NOTES (``notes``) are ASCII ENGLISH ON PURPOSE — they are written into the
``.nc`` file, which :func:`~gerber2ldi.backends.gcode.write_gcode` writes with
``encoding="ascii"``. A translated note would either break the write or reach the
machine as question marks, so notes never go through ``_()``; messages for the
operator on stdout and stderr do.

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
class PowerBand:
    """A y-range of the FIGURE and the power fraction that exposes it.

    Figure millimetres, not machine ones: the band belongs to the geometry, and the
    row that covers it is found during the raster. Bands must be sorted and must not
    overlap — a row belongs to exactly one of them (half-open on the upper edge).
    """

    y0_mm: float
    y1_mm: float
    power: float


@dataclass(frozen=True, slots=True)
class Dose:
    """A band with the ``S`` the machine will see — printed out and in the header."""

    y0_mm: float
    y1_mm: float
    power: float
    s_value: int


def _row_pass(row: ScanRow, overscan_mm: float) -> tuple[float, float] | None:
    """The pass rule itself: the row's own copper plus the strip, or nothing at all.

    Module level so that the length of the run can be counted BEFORE the job exists — the
    header note is built in `build_job`, and it must count the same passes the writer
    writes, not a second opinion about them.
    """
    if not row.segments:
        return None
    return (row.segments[0][0] - overscan_mm,
            row.segments[-1][1] + overscan_mm)


def _pass_lengths(rows: tuple[ScanRow, ...], overscan_mm: float,
                  box: Box) -> tuple[float, float]:
    """The run the head really travels, and the run it would travel at full width.

    ``exposed`` sums the passes of the rows that have copper, run-ups included; a row with
    nothing on it costs nothing, which is what the trimming bought. ``full_width`` is what
    the SAME rows would cost if each of them crossed the whole job frame — the number the
    operator compares against.
    """
    exposed = 0.0
    for row in rows:
        span = _row_pass(row, overscan_mm)
        if span is not None:
            exposed += span[1] - span[0]
    frame = (box[2] + overscan_mm) - (box[0] - overscan_mm)
    return exposed, len(rows) * frame


def format_duration(seconds: float) -> str:
    """A duration as ``4 min 06 s``, rounded DOWN — the line promises ``>=``.

    A fraction of a second would be a claim about a speed the machine was never measured
    at; whole seconds below the estimate keep the promise true.
    """
    total = int(seconds)
    minutes, rest = divmod(total, 60)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"


@dataclass(frozen=True, slots=True)
class Job:
    """Machine-space rows plus everything the G-code writer needs.

    ``s_values`` is the power PER ROW, empty for a uniform job where ``s_value``
    covers every row. :attr:`row_s_values` gives the writer one shape for both cases.
    ``frame_box`` is the board outline in machine coordinates when the caller passed
    one — the thing both sides are mirrored and placed by.
    """

    rows: tuple[ScanRow, ...]
    box: Box
    overscan_mm: float
    speed_mm_s: float
    s_value: int
    power_fraction: float | None
    side: str
    process: str
    place_mm: tuple[float, float] = (0.0, 0.0)
    warnings: tuple[str, ...] = ()
    s_values: tuple[int, ...] = ()
    doses: tuple[Dose, ...] = ()
    notes: tuple[str, ...] = ()
    frame_box: Box | None = None

    def __post_init__(self) -> None:
        if self.s_values and len(self.s_values) != len(self.rows):
            raise JobError(
                _("The job carries {given} row powers for {rows} rows")
                .format(given=len(self.s_values), rows=len(self.rows)))

    @property
    def row_s_values(self) -> tuple[int, ...]:
        """One ``S`` per row: the band value, or the uniform one repeated."""
        return self.s_values or (self.s_value,) * len(self.rows)

    @property
    def feed_mm_per_min(self) -> float:
        """GRBL's ``F`` is millimetres per MINUTE; the profile speaks mm/s.

        The conversion lives here and only here: writing ``F50`` for 50 mm/s runs the
        machine sixty times slower and deposits sixty times the dose.
        """
        return self.speed_mm_s * 60.0

    @property
    def pass_start_x(self) -> float:
        """The JOB frame: the figure edge minus the strip (table check and placement).

        Not the pass of a single row — :meth:`row_pass` is.
        """
        return self.box[0] - self.overscan_mm

    @property
    def pass_end_x(self) -> float:
        return self.box[2] + self.overscan_mm

    def row_pass(self, row: ScanRow) -> tuple[float, float] | None:
        """Where THIS row's pass begins and ends, or ``None`` when the row is empty.

        The strip before the first segment and after the last one is the run-up: the
        laser must already be at full speed on the first lit pixel and keep it past the
        last one (design §4.2). Everything beyond that strip is travel over empty table —
        the job used to cross the whole frame on every row, so a row with one pad at the
        left edge drove the full width of the board and came back (Денис 22.10: the
        exposure took as long as the pocket in GGEasy).

        ``pass_start_x``/``pass_end_x`` stay the JOB frame: the table check and the
        placement are computed from it, so trimming a row never moves the figure, and a
        new program with the same ``--place`` still puts the copper in the same machine
        coordinates. The pass of a row always runs in +X, so the slack of the belt is
        taken up by the run-up in the same direction every time.
        """
        return _row_pass(row, self.overscan_mm)

    def pass_lengths(self) -> tuple[float, float]:
        """(exposed run, full-width run) in millimetres — both from :meth:`row_pass`."""
        return _pass_lengths(self.rows, self.overscan_mm, self.box)

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


def _check_bands(bands: tuple[PowerBand, ...]) -> None:
    """Bands come from a pattern as data: finite, ordered, disjoint, above zero."""
    previous: PowerBand | None = None
    for band in bands:
        for field_name, value in (("y0_mm", band.y0_mm), ("y1_mm", band.y1_mm),
                                  ("power", band.power)):
            if not math.isfinite(value):
                raise JobError(
                    _("Power band {y0} … {y1} mm has a non-finite {field}")
                    .format(y0=band.y0_mm, y1=band.y1_mm, field=field_name))
        if band.y1_mm <= band.y0_mm:
            raise JobError(
                _("Power band {y0} … {y1} mm is empty: the upper edge must be above the lower one")
                .format(y0=band.y0_mm, y1=band.y1_mm))
        if band.power <= 0.0:
            raise JobError(
                _("Power band {y0} … {y1} mm asks for {power}: the fraction must be above zero")
                .format(y0=band.y0_mm, y1=band.y1_mm, power=band.power))
        if previous is not None and band.y0_mm < previous.y1_mm:
            raise JobError(
                _("Power bands {a0} … {a1} and {b0} … {b1} mm overlap")
                .format(a0=previous.y0_mm, a1=previous.y1_mm,
                        b0=band.y0_mm, b1=band.y1_mm))
        previous = band


def _doses(bands: tuple[PowerBand, ...], machine: Machine) -> tuple[Dose, ...]:
    """Bands → bands with the ``S`` word; every level passes the ceiling check."""
    _check_bands(bands)
    return tuple(Dose(band.y0_mm, band.y1_mm, band.power,
                      _s_value(band.power, machine))
                 for band in bands)


def _band_index(figure_y_mm: float, doses: tuple[Dose, ...]) -> int | None:
    """Which band owns the row that samples ``figure_y_mm``; half-open on top."""
    for index, dose in enumerate(doses):
        if dose.y0_mm <= figure_y_mm < dose.y1_mm:
            return index
    return None


def _compensation_note(radius: float | None) -> str:
    """ASCII English, like every header note — see the module docstring."""
    if radius is None:
        return "spot compensation: off"
    return f"spot compensation: r={radius:.3f} mm"


FRAME_EDGE_TOLERANCE_MM = 1e-6
"""How far copper may pass the outline before it is called out, in millimetres."""


def _frame_box(frame: Layer, x_scale: float, y_scale: float,
               tan_skew: float) -> Box:
    """The board outline in MACHINE coordinates, put through the same steps as rows.

    The frame is a rectangle in figure millimetres. A machine row commanded to
    ``height`` samples the figure at ``height · y_scale``, so a figure height ``y``
    lands on machine height ``y / y_scale``; x is commanded as ``figure_x / x_scale``;
    and the skew shifts every row by ``−y · tg θ``. Four corners through those steps
    give the box the table check and the placement are allowed to see.
    """
    x0, y0, x1, y1 = frame.bbox
    machine_ys = (y0 / y_scale, y1 / y_scale)
    xs = tuple(x / x_scale - y * tan_skew for x in (x0, x1) for y in machine_ys)
    return (min(xs), min(machine_ys), max(xs), max(machine_ys))


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
              process: str = PROCESS_ETCH, power: float | None = None,
              power_bands: tuple[PowerBand, ...] = (),
              notes: tuple[str, ...] = (),
              frame: Layer | None = None,
              board: bool = True,
              spot_compensation: bool = True,
              place_mm: tuple[float, float] = (0.0, 0.0)) -> Job:
    """Turn a layer into machine rows, applying the calibration and the limits.

    ``power_bands`` is how a calibration pattern gets a different power on each band
    of rows (a dose strip). It is refused together with spot compensation, because
    that step adds and removes rows: a band would then no longer own its rows, and
    attaching power by y afterwards would be a guess dressed up as a measurement.

    ``frame`` is the board outline (usually Edge_Cuts). It — not this layer's copper —
    defines the mirror axis, the placement and the table check, so the two sides of one
    board land on the same rectangle even though F_Cu and B_Cu have different copper.
    Without it each side is mirrored and placed by its own copper: correct for ONE side
    (including copper at the bottom, ``--side bottom``), wrong for a second side of the
    same board — hence a warning, not a refusal.

    ``board`` says whether the layer IS a board. A calibration pattern is not: it has no
    second side, so the missing-frame warning would hand the operator a flag with nothing
    to point at (the finding of the 22.10 run: ``pattern bars`` printed a board hint).
    Pass ``board=False`` for a pattern. The header line ``frame: none (…)`` stays either
    way: it states where the mirror axis and the placement came from, which is true for
    both a board and a pattern.
    """
    _check_choice(side, SIDES, _("side"))
    _check_choice(process, PROCESSES, _("process"))
    if machine.table.origin != SUPPORTED_ORIGIN:
        raise JobError(
            _("Table origin {origin} is not supported yet: only {supported}")
            .format(origin=machine.table.origin, supported=SUPPORTED_ORIGIN))
    if power is None and not power_bands:
        raise JobError(_("Power is required: one value, or bands for a dose strip"))
    if power is not None and power_bands:
        raise JobError(_("Either one power or power bands, not both"))
    if power_bands and spot_compensation:
        raise JobError(
            _("Power bands cannot be combined with spot compensation: it adds and removes rows, so a band would no longer own its rows (a dose strip runs with --no-spot-compensation)"))
    s_value = _s_value(power, machine) if power is not None else 0
    doses = _doses(power_bands, machine) if power_bands else ()

    calibration = machine.calibration
    warnings: list[str] = list(machine.warnings)
    if frame is None and board:
        warnings.append(
            _("No common frame: the mirror and the placement use this layer's own copper; a second side of the same board needs --frame"))

    radius: float | None = None
    if spot_compensation:
        radius = machine.require_spot_radius()
    else:
        warnings.append(_("Spot compensation is off: every line is exposed wider than drawn by the spot radius"))

    x_scale, y_scale = calibration.scale_x, calibration.scale_y
    x0, y0, x1, y1 = layer.bbox
    machine_box = (x0 / x_scale, y0 / y_scale, x1 / x_scale, y1 / y_scale)

    # The band of a row is decided HERE, at the raster, where a commanded row knows
    # the figure height it samples (`height · y_scale`). Every later step keeps the
    # order and the count of the rows (compensation, which does not, is refused).
    rows_list: list[ScanRow] = []
    s_rows: list[int] = []
    band_rows = [0] * len(doses)
    for row in iter_rows(layer.shapes, machine_box,
                         machine.scan.row_step_mm, y_scale=y_scale):
        rows_list.append(row)
        if not doses:
            s_rows.append(s_value)
            continue
        index = _band_index(row.y_mm * y_scale, doses)
        if index is None:
            if row.segments:
                raise JobError(
                    _("Row at {y} mm exposes copper outside every power band: the pattern and its bands disagree")
                    .format(y=round(row.y_mm, 3)))
            s_rows.append(0)
            continue
        band_rows[index] += 1
        s_rows.append(doses[index].s_value)
    for dose, count in zip(doses, band_rows):
        if count == 0:
            raise JobError(
                _("Power band {y0} … {y1} mm covers no machine row at a {step} mm row step")
                .format(y0=dose.y0_mm, y1=dose.y1_mm, step=machine.scan.row_step_mm))
    rows = tuple(rows_list)

    # 2. what will be exposed: mirror for the bottom side about the FRAME, not about
    # its own copper: F_Cu and B_Cu have different bounding boxes, so a mirror about
    # the copper puts the sides of one board at different places on the table.
    if side == SIDE_BOTTOM:
        figure = ink_box(rows) if frame is None else frame.bbox
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
    # `scale_x` is real / commanded, so what must be COMMANDED is figure / scale_x.
    rows = tuple(ScanRow(row.y_mm, scale(row.segments, 1.0 / x_scale)) for row in rows)
    if calibration.skew_deg:
        # The machine bends the other way: real x = commanded x + y·tg θ (θ > 0 is
        # the real Y axis leaning towards +X, design §17 "Соглашение о знаках"), so
        # the correction is commanded x = figure x − y·tg θ — MINUS.
        rows = tuple(apply_skew(rows, -math.tan(math.radians(calibration.skew_deg))))

    # 5. placement: the run-up start and the bottom edge go to place_mm — of the
    # FRAME when there is one, so both sides start at the same table point.
    overscan = machine.scan.overscan_mm
    ink = ink_box(rows)
    tan_skew = math.tan(math.radians(calibration.skew_deg))
    frame_machine = (_frame_box(frame, x_scale, y_scale, tan_skew)
                     if frame is not None else None)
    placing = frame_machine if frame_machine is not None else ink
    place_x, place_y = float(place_mm[0]), float(place_mm[1])
    shift_x = place_x - (placing[0] - overscan)
    shift_y = place_y - placing[1]
    rows = tuple(ScanRow(row.y_mm + shift_y, shift(row.segments, shift_x))
                 for row in rows)
    ink = (ink[0] + shift_x, ink[1] + shift_y, ink[2] + shift_x, ink[3] + shift_y)
    frame_box: Box | None = None
    if frame_machine is not None:
        frame_box = (frame_machine[0] + shift_x, frame_machine[1] + shift_y,
                     frame_machine[2] + shift_x, frame_machine[3] + shift_y)
        if (ink[0] < frame_box[0] - FRAME_EDGE_TOLERANCE_MM
                or ink[1] < frame_box[1] - FRAME_EDGE_TOLERANCE_MM
                or ink[2] > frame_box[2] + FRAME_EDGE_TOLERANCE_MM
                or ink[3] > frame_box[3] + FRAME_EDGE_TOLERANCE_MM):
            warnings.append(
                _("Copper sticks out of the board frame: x {x0} … {x1} against {f0} … {f1}, y {y0} … {y1} against {f2} … {f3} mm")
                .format(x0=round(ink[0], 3), x1=round(ink[2], 3),
                        f0=round(frame_box[0], 3), f1=round(frame_box[2], 3),
                        y0=round(ink[1], 3), y1=round(ink[3], 3),
                        f2=round(frame_box[1], 3), f3=round(frame_box[3], 3)))

    # The table must hold everything the pass touches: the frame is the honest size of
    # the board, but copper that sticks out would still be exposed off the table.
    checked = ink if frame_box is None else (
        min(ink[0], frame_box[0]), min(ink[1], frame_box[1]),
        max(ink[2], frame_box[2]), max(ink[3], frame_box[3]))
    _check_table(machine, checked, overscan)

    # A stepper cannot stop between its own microsteps: an edge shorter than one step
    # lands on the nearest step instead, so a fine feature comes out coarse. COUNTED,
    # not refused — a board has many such edges and refusing would block every job.
    step_x = machine.motor.step_x_mm
    short = sum(1 for row in rows for start, end in row.segments
                if end - start < step_x)
    if short:
        warnings.append(
            _("{count} segments are shorter than the X motor step {step} mm: their edges snap to it, so the exposed ends move by up to half a step")
            .format(count=short, step=round(step_x, 4)))

    if radius is not None:
        period = machine.scan.speed_mm_s / machine.laser.pwm_hz
        if period > radius:
            warnings.append(
                _("PWM period {period} mm is wider than the spot radius {radius} mm: dashes, not a line")
                .format(period=round(period, 5), radius=round(radius, 5)))

    header = list(notes)
    header.append(_compensation_note(radius))
    if frame_box is None:
        header.append("frame: none (mirror axis and placement from this layer's own copper)")
    else:
        header.append(f"frame: x {frame_box[0]:.3f}-{frame_box[2]:.3f} "
                      f"y {frame_box[1]:.3f}-{frame_box[3]:.3f} mm "
                      f"(board outline: mirror axis and placement)")
    header.append(f"calibration in force: scale_x={x_scale:.6f} "
                  f"scale_y={y_scale:.6f} skew={calibration.skew_deg:.4f} deg")
    # The power word is a FRACTION of $30, so the controller setting is part of the
    # program's meaning: the same file with another $30 exposes another dose. The note
    # avoids a bare "S" so that a parser looking for power words cannot trip on it.
    header.append(f"GRBL: $32=1 (laser mode), "
                  f"$30={machine.laser.s_max:.0f} "
                  f"(max spindle speed: the power word is a fraction of it)")
    for index, dose in enumerate(doses, start=1):
        header.append(f"dose {index}/{len(doses)}: y {dose.y0_mm:.3f}-{dose.y1_mm:.3f} mm "
                      f"(figure), power {dose.power:.4f}, S {dose.s_value}")

    # What the trimming bought, in ONE line: the run the head really travels against the
    # run the same rows would cost at full width. ASCII English like every header note
    # (the file is written with encoding="ascii"); the time is a LOWER bound on purpose —
    # the rapids, the accelerations and M4/M5 are not in it, and the profile has no G0
    # speed to count them with.
    exposed, full_width = _pass_lengths(rows, overscan, ink)
    share = (exposed / full_width - 1.0) * 100.0 if full_width else 0.0
    header.append(f"passes: {exposed:.1f} mm exposed run "
                  f"(full-width would be {full_width:.1f} mm, {share:+.0f}%), "
                  f">= {format_duration(exposed / machine.scan.speed_mm_s)} "
                  f"at {machine.scan.speed_mm_s:g} mm/s")

    return Job(rows=rows, box=ink, overscan_mm=overscan,
               speed_mm_s=machine.scan.speed_mm_s,
               s_value=s_value if power is not None else max(d.s_value for d in doses),
               power_fraction=power, side=side, process=process,
               place_mm=(place_x, place_y), warnings=tuple(warnings),
               s_values=tuple(s_rows) if doses else (), doses=doses,
               notes=tuple(header), frame_box=frame_box)
