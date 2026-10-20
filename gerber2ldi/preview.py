# gerber2ldi/preview.py
"""A PNG for the eyes, ≈2000 px on the long side, built from the ROWS (plan Ш4).

Not from the TIFF: the rows are the truth and the job is built from them, so a
picture drawn from anything else can disagree with what the machine will do.

Orientation (contracts.md): rows ascend in `y`, an image file descends, so the top
of the picture is the largest `y`. The flip happens once, here, when the file is
written.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from .raster import rows_to_image
from .scan import ScanRow, union

MAX_PREVIEW_PX = 2000
BBox = tuple[float, float, float, float]


def preview_step_mm(box: BBox, long_side_px: int = MAX_PREVIEW_PX) -> float:
    """Pixel size that makes the LONG side ``long_side_px`` wide."""
    if long_side_px <= 0:
        raise ValueError(f"long side must be positive, got {long_side_px!r}")
    longest = max(box[2] - box[0], box[3] - box[1])
    if longest <= 0.0:
        raise ValueError(f"the field is empty: {box!r}")
    return longest / float(long_side_px)


def _merge_rows_to_step(rows: Iterable[ScanRow], box: BBox, step: float) -> list[ScanRow]:
    """Resample rows onto the pixel grid: one image row per ``step`` of Y.

    Without this the picture keeps one image row per MACHINE row, so a 10 × 10 mm
    field with 20 rows at 0.1 mm came out 20 px tall and 100 px wide — squashed,
    and a squashed preview is exactly what made the step-0 eyeball check useless.
    Rows inside one pixel band are UNIONed, so a feature thinner than a pixel still
    shows up instead of being dropped by nearest-neighbour sampling.
    """
    y0, y1 = box[1], box[3]
    count = max(1, int(round((y1 - y0) / step)))
    buckets: list[tuple] = [()] * count
    for row in rows:
        index = int((row.y_mm - y0) / step)
        index = min(count - 1, max(0, index))
        buckets[index] = union(buckets[index], row.segments)
    return [ScanRow(y0 + (index + 0.5) * step, segments)
            for index, segments in enumerate(buckets)]


def write_preview(path: str | Path, rows: Iterable[ScanRow], box: BBox, *,
                  long_side_px: int = MAX_PREVIEW_PX) -> Path:
    step = preview_step_mm(box, long_side_px)
    resampled = _merge_rows_to_step(rows, box, step)
    picture = np.flipud(rows_to_image(resampled, box, step))  # top = largest y
    # "L" then threshold: fromarray(bool, mode="1") mangles the picture (step 2).
    image = Image.fromarray((picture * 255).astype(np.uint8), mode="L").convert("1")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return target
