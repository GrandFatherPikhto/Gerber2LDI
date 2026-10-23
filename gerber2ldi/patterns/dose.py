# gerber2ldi/patterns/dose.py
"""The dose strip: tiles stacked in Y, each exposed with its own power OR its own speed.

Dose acts MULTIPLICATIVELY, so the levels are a geometric series — each tile is
``ratio`` times the previous one, ``ratio = (stop/start)^(1/(N-1))``. The mapping
"tile, bottom to top -> y range, power, speed, S" is printed to stdout and written into
the ``.nc`` header, because there is nothing on the board to label it with.

TWO WAYS TO VARY THE DOSE, never both (plan С2 of `plan_2026_10_23_dose_by_speed`):

  * ``powers`` — one fraction per tile, every tile crossing at the profile's own speed.
    This is the strip the old module needed, where ``S`` was the dose;
  * ``speeds`` — one speed per tile in mm/s and ONE power for all of them. With an on/off
    laser the power word is a constant (``S`` = ``s_max``) and the only handle on the dose
    is how fast the head moves: dose ∝ 1/v, so the tiles run BOTTOM TO TOP with the speed
    FALLING and the dose rising. Equal neighbours are refused: a strip whose two tiles
    cannot be told apart is a wasted exposure, not a level.

Every level passes the hard checks in `job.job`: the ceiling (`_s_value`) and the profile's
single speed rule (`Machine.speed_problem`). A level above the ceiling or above the machine
limit is a REFUSAL, never a clamp. A strip that silently stopped rising would send the
operator hunting for a sensitivity the machine never reached.
"""
from __future__ import annotations

import math

from ..job import Machine, PowerBand
from .base import Pattern, calibration_note
from .shapes import layer_of, rectangle

DEFAULT_TILE_WIDTH_MM = 20.0
DEFAULT_TILE_HEIGHT_MM = 10.0


def _series(start: float, stop: float, count: int, what: str) -> tuple[float, ...]:
    """A geometric series of ``count`` levels from ``start`` to ``stop``, both included.

    One place for the arithmetic: powers and speeds are the same geometric ladder, only
    the words around them differ, and ``stop < start`` (a falling speed series) is legal.
    """
    if count < 2:
        raise ValueError(f"a geometric series needs at least two levels, got {count}")
    if not all(math.isfinite(value) and value > 0.0 for value in (start, stop)):
        raise ValueError(f"{what} must be finite and positive, got {start!r} ... {stop!r}")
    ratio = (stop / start) ** (1.0 / (count - 1))
    return tuple(start * ratio ** index for index in range(count))


def powers_from_series(start: float, stop: float, count: int) -> tuple[float, ...]:
    """A geometric series of `count` powers from `start` to `stop`, both included."""
    return _series(start, stop, count, "powers")


def speeds_from_series(start: float, stop: float, count: int) -> tuple[float, ...]:
    """A geometric series of `count` speeds in mm/s from `start` down to `stop`.

    ``start`` is the FASTEST tile, i.e. the lowest dose at the bottom of the strip:
    ``--speed-from 80 --speed-to 5`` gives 80, 57, 40 … 5 mm/s. A series that does not
    fall cannot raise the dose upwards, so it is refused here rather than drawn.
    """
    if start <= stop:
        raise ValueError(
            f"a speed series falls from the fastest to the slowest, got {start!r} ... {stop!r}")
    return _series(start, stop, count, "speeds")


def dose(machine: Machine, powers: tuple[float, ...] = (), *,
         speeds: tuple[float, ...] = (), power: float | None = None,
         tile_width_mm: float = DEFAULT_TILE_WIDTH_MM,
         tile_height_mm: float = DEFAULT_TILE_HEIGHT_MM) -> Pattern:
    """Tiles from the origin upwards; tile `i` owns figure y [i·h, (i+1)·h).

    ``powers`` (one per tile) and ``speeds`` (one per tile, with one ``power`` for all)
    are alternatives — the CLI refuses them together as well, this is the second belt.
    """
    levels = tuple(float(level) for level in powers)
    rate = tuple(float(level) for level in speeds)
    if levels and rate:
        raise ValueError("a dose strip varies either the power or the speed, not both")
    if levels and power is not None:
        raise ValueError("one power goes with --speeds; with --powers the levels are powers")
    if rate and power is None:
        raise ValueError("a speed strip needs one power fraction for all its tiles")
    if not levels and not rate:
        raise ValueError("at least one dose level is required")
    if any(not math.isfinite(value) or value <= 0.0 for value in levels + rate):
        raise ValueError(
            f"dose levels must be finite and positive, got {levels + rate!r}")
    if rate:
        # Dose ∝ 1/v, so the strip rises only if the speeds fall as the tiles go up.
        for lower, upper in zip(rate, rate[1:]):
            if upper >= lower:
                raise ValueError(
                    f"speeds must fall from the bottom tile up (dose rises), "
                    f"got {lower!r} then {upper!r}")
    if not (tile_width_mm > 0.0 and tile_height_mm > 0.0):
        raise ValueError(
            f"tile size must be positive, got {tile_width_mm!r} x {tile_height_mm!r}")

    shapes = tuple(rectangle(0.0, index * tile_height_mm, tile_width_mm,
                             (index + 1) * tile_height_mm)
                   for index in range(len(levels) or len(rate)))
    if rate:
        bands = tuple(PowerBand(index * tile_height_mm, (index + 1) * tile_height_mm,
                                float(power), speed)
                      for index, speed in enumerate(rate))
        rising = "bottom to top = falling speed, rising dose"
    else:
        bands = tuple(PowerBand(index * tile_height_mm, (index + 1) * tile_height_mm,
                                level)
                      for index, level in enumerate(levels))
        rising = "bottom to top = rising power"
    notes = (
        f"pattern: dose strip, {len(bands)} tiles stacked in Y, {rising}",
        f"tiles: {tile_width_mm:.3f} x {tile_height_mm:.3f} mm",
        calibration_note(machine, applied=True),
    )
    return Pattern(layer=layer_of(shapes), machine=machine, notes=notes, bands=bands)
