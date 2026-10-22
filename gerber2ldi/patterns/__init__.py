"""Jobs built as ordinary geometry: test bars, dose strip, skew square, grid.

Every pattern returns a :class:`Pattern` — a synthetic `Layer` of `Shape` polygons,
the machine model it asks for, ASCII notes for the `.nc` header and, for the dose
strip, power bands. It then goes through `build_job` like a Gerber layer: one path
into a job, guarded by the same cells.

Three rules these patterns exist to keep:

  * a pattern is not drawn through the calibration it measures — the skew square
    strips scale and skew by default (see `patterns.square`);
  * the bars, the dose strip and the grid run with spot compensation OFF by default:
    the spot is what the bars MEASURE, so requiring it first would be a closed circle,
    and a grid drawn through an unmeasured radius would hide what it exists to show;
  * every power level passes the hard ceiling check in the job — refusals, no clamps.

The grid (`patterns.grid`) is the coating test rather than a measurement: it fills a
rectangle with lines so that the whole area can be inspected after development.
"""
from .bars import DEFAULT_LENGTH_MM, DEFAULT_WIDTHS_MM, bars
from .base import Pattern, calibration_note
from .dose import DEFAULT_TILE_HEIGHT_MM, DEFAULT_TILE_WIDTH_MM, dose, powers_from_series
from .grid import (DEFAULT_GRID_GAP_MM, DEFAULT_GRID_HEIGHT_MM, DEFAULT_GRID_LINE_MM,
                   DEFAULT_GRID_WIDTH_MM, GRID_DIRECTIONS, fit_lines, grid)
from .square import (DEFAULT_LINE_WIDTH_MM, DEFAULT_SIDE_MM, SquareMeasurement,
                     diagonals_from_rows, measure_from_rows, skew_from_diagonals,
                     square, update_formulas, without_calibration)

__all__ = [
    "DEFAULT_GRID_GAP_MM",
    "DEFAULT_GRID_HEIGHT_MM",
    "DEFAULT_GRID_LINE_MM",
    "DEFAULT_GRID_WIDTH_MM",
    "DEFAULT_LENGTH_MM",
    "DEFAULT_LINE_WIDTH_MM",
    "DEFAULT_SIDE_MM",
    "DEFAULT_TILE_HEIGHT_MM",
    "DEFAULT_TILE_WIDTH_MM",
    "DEFAULT_WIDTHS_MM",
    "GRID_DIRECTIONS",
    "Pattern",
    "SquareMeasurement",
    "bars",
    "calibration_note",
    "diagonals_from_rows",
    "dose",
    "fit_lines",
    "grid",
    "measure_from_rows",
    "powers_from_series",
    "skew_from_diagonals",
    "square",
    "update_formulas",
    "without_calibration",
]
