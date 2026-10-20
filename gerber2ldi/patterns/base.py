# gerber2ldi/patterns/base.py
"""What every calibration pattern returns: geometry, notes, power bands.

A pattern is ORDINARY GEOMETRY — the same `Layer` of `Shape` polygons the Gerber
reader produces — so it goes through `build_job` unchanged: Y scale, mirror,
polarity, compensation, X scale, skew, placement and the table check are the same
code that draws a real board. There is no second path into a job.

`notes` are ASCII English and go into the `.nc` header (see `job.job`): the header
is written with ``encoding="ascii"``, and a translated note would reach the machine
as question marks.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..job import Machine, PowerBand
from ..source import Layer


@dataclass(frozen=True, slots=True)
class Pattern:
    """A calibration pattern: the figure, the machine model it asks for, its notes."""

    layer: Layer
    machine: Machine
    notes: tuple[str, ...] = ()
    bands: tuple[PowerBand, ...] = ()
    warnings: tuple[str, ...] = ()


def calibration_note(machine: Machine, *, applied: bool) -> str:
    """One header line saying whether the measured calibration was used, with values.

    A pattern must not be drawn through the calibration it measures, so
    ``applied=False`` is the normal case for the skew square; then the measurement is
    ABSOLUTE and goes into the calibration file as is. With ``applied=True`` the
    measurement is a RESIDUAL: it is added to these values, not written over them.
    """
    calibration = machine.calibration
    values = (f"scale_x={calibration.scale_x:.6f} "
              f"scale_y={calibration.scale_y:.6f} "
              f"skew={calibration.skew_deg:.4f} deg")
    if applied:
        return f"calibration: applied ({values})"
    return f"calibration: NOT applied, the pattern measures it; the profile has {values}"
