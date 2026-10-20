"""Raster: geometry → machine rows (no image needed), plus the TIFF preview."""
from .rows import (ROW_CENTRE, iter_rows, polygon_intervals, row_count,
                   row_segments, row_y)
from .tiff import rows_to_image, write_tiff

__all__ = [
    "ROW_CENTRE",
    "iter_rows",
    "polygon_intervals",
    "row_count",
    "row_segments",
    "row_y",
    "rows_to_image",
    "write_tiff",
]
