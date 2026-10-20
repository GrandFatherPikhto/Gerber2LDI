# gerber2ldi/patterns/dose.py
"""The dose strip: tiles stacked in Y, each exposed with its own power.

Dose acts MULTIPLICATIVELY, so the levels are a geometric series — each tile is
``ratio`` times the previous one, ``ratio = (stop/start)^(1/(N-1))``. The mapping
"tile, bottom to top -> y range, power, S" is printed to stdout and written into the
``.nc`` header, because there is nothing on the board to label it with.

Every level passes the hard ceiling check in `job.job` (``_s_value``): a level above
``usable_fraction`` is a REFUSAL, never a clamp. A strip that silently stopped rising
would send the operator hunting for a sensitivity the machine never reached.
"""
from __future__ import annotations

import math

from ..job import Machine, PowerBand
from .base import Pattern, calibration_note
from .shapes import layer_of, rectangle

DEFAULT_TILE_WIDTH_MM = 20.0
DEFAULT_TILE_HEIGHT_MM = 10.0


def powers_from_series(start: float, stop: float, count: int) -> tuple[float, ...]:
    """A geometric series of `count` powers from `start` to `stop`, both included."""
    if count < 2:
        raise ValueError(f"a geometric series needs at least two levels, got {count}")
    if not all(math.isfinite(value) and value > 0.0 for value in (start, stop)):
        raise ValueError(f"powers must be finite and positive, got {start!r} ... {stop!r}")
    ratio = (stop / start) ** (1.0 / (count - 1))
    return tuple(start * ratio ** index for index in range(count))


def dose(machine: Machine, powers: tuple[float, ...], *,
         tile_width_mm: float = DEFAULT_TILE_WIDTH_MM,
         tile_height_mm: float = DEFAULT_TILE_HEIGHT_MM) -> Pattern:
    """Tiles from the origin upwards; tile `i` owns figure y [i·h, (i+1)·h)."""
    powers = tuple(float(power) for power in powers)
    if not powers:
        raise ValueError("at least one power level is required")
    if any(not math.isfinite(power) or power <= 0.0 for power in powers):
        raise ValueError(f"power levels must be finite and positive, got {powers!r}")
    if not (tile_width_mm > 0.0 and tile_height_mm > 0.0):
        raise ValueError(
            f"tile size must be positive, got {tile_width_mm!r} x {tile_height_mm!r}")

    shapes = tuple(rectangle(0.0, index * tile_height_mm, tile_width_mm,
                             (index + 1) * tile_height_mm)
                   for index in range(len(powers)))
    bands = tuple(PowerBand(index * tile_height_mm, (index + 1) * tile_height_mm, power)
                  for index, power in enumerate(powers))
    notes = (
        f"pattern: dose strip, {len(powers)} tiles stacked in Y, bottom to top = rising power",
        f"tiles: {tile_width_mm:.3f} x {tile_height_mm:.3f} mm",
        calibration_note(machine, applied=True),
    )
    return Pattern(layer=layer_of(shapes), machine=machine, notes=notes, bands=bands)
