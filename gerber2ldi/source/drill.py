# gerber2ldi/source/drill.py
"""Input: an Excellon drill file → the holes as `(x, y, d)` in millimetres.

Load and list only: alignment against the copper (design §6) is a later step, and
the machine may not even drill this board (§10.1). Keeping the loader dumb now
means the alignment work later has one source of truth for hole positions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import InputFileError
from ..i18n import _
from .layer import quiet_library


@dataclass(frozen=True, slots=True)
class Hole:
    x_mm: float
    y_mm: float
    diameter_mm: float


def load_drill(path: str | Path) -> tuple[Hole, ...]:
    """Read an Excellon file. Raises :class:`InputFileError` when it cannot be read."""
    from gerbonara.excellon import ExcellonFile
    from gerbonara.utils import MM

    source = Path(path)
    if not source.is_file():
        raise InputFileError(_("File not found: {path}").format(path=source))
    holes: list[Hole] = []
    try:
        with quiet_library():
            drill = ExcellonFile.open(str(source))
            for obj in drill.objects:
                for prim in obj.to_primitives(unit=MM):
                    x = getattr(prim, "x", None)
                    y = getattr(prim, "y", None)
                    # gerbonara turns a drill hit into a Circle, which carries a
                    # RADIUS (`r`), not a diameter — measured, not assumed.
                    diameter = getattr(prim, "diameter", None)
                    if diameter is None:
                        radius = getattr(prim, "r", None)
                        diameter = None if radius is None else 2.0 * radius
                    if x is None or y is None or diameter is None:
                        continue
                    holes.append(Hole(float(x), float(y), float(diameter)))
    except InputFileError:
        raise
    except Exception as exc:  # noqa: BLE001 — the library's failure is our refusal
        raise InputFileError(_("Cannot read drill file {path}: {error}")
                             .format(path=source, error=exc)) from exc
    return tuple(holes)
