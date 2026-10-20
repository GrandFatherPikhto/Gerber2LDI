# gerber2ldi/raster/tiff.py
"""A picture for the eyes: 1-bit TIFF with CCITT G4 compression (plan Ш2).

The TIFF is a PREVIEW, not a stage of the pipeline: the job is built from
:class:`~gerber2ldi.scan.ScanRow` objects, and nothing downstream reads this file
back. It exists so that a human can put the layer next to GerbView.

ORIENTATION (contracts.md): rows ascend in `y`, image files descend — row 0 is the
top. The flip happens HERE, once, when the file is written; doing it in the
rasteriser or in `scan/` would corrupt the data those layers hand on.

WHITE = copper, BLACK = no copper, the same convention as the step-0 probe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from ..scan import ScanRow

BBox = tuple[float, float, float, float]


def rows_to_image(rows: Iterable[ScanRow], box: BBox,
                  step_x_mm: float) -> np.ndarray:
    """Collect rows into a boolean picture: True = copper (white).

    The whole picture lives in memory, which is why this is for previews only —
    at a 2.5 µm X step a 100×100 mm field is 200 MB, at 10 µm it is 12.5 MB.
    """
    if step_x_mm <= 0.0:
        raise ValueError(f"X step must be positive, got {step_x_mm!r}")
    x0, _y0, x1, _y1 = box
    columns = int(round((x1 - x0) / step_x_mm))
    if columns <= 0:
        raise ValueError(f"field is narrower than one pixel: {x1 - x0!r} mm")
    collected = list(rows)
    picture = np.zeros((len(collected), columns), dtype=bool)
    for index, row in enumerate(collected):
        for start, end in row.segments:
            first = max(0, int(round((start - x0) / step_x_mm)))
            last = min(columns, int(round((end - x0) / step_x_mm)))
            if last > first:
                picture[index, first:last] = True
    return picture


def write_tiff(path: str | Path, rows: Iterable[ScanRow], box: BBox,
               step_x_mm: float, *, top_down: bool = True) -> Path:
    """Write the preview. ``top_down=True`` flips the raster as the file needs."""
    picture = rows_to_image(rows, box, step_x_mm)
    if top_down:
        picture = np.flipud(picture)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # NOT Image.fromarray(picture, mode="1"): given a bool array that path reads
    # one BYTE per pixel as a bit, so the picture comes out mangled (measured:
    # 924 of 1000 copper pixels survived). Going through "L" and letting Pillow
    # threshold is exact for pure black/white and keeps group4 happy.
    image = Image.fromarray((picture * 255).astype(np.uint8), mode="L").convert("1")
    image.save(target, format="TIFF", compression="group4")
    return target
