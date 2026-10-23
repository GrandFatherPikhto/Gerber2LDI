# gerber2ldi/job/passes.py
"""A row's pass and the length of the run (plans `plan_2026_10_22_trim_passes` Т1–Т2).

A pass belongs to the ROW, not to the job frame: it starts one ``overscan_mm`` before the
row's OWN first lit pixel and ends one ``overscan_mm`` after its own last one, always in
+X, so the run-up takes up the belt slack in the same direction on every row. A row with
no copper gets NO pass at all — no rapid, no ``M4``: the head stays where the previous row
left it, and the next ``G0`` is a diagonal that GRBL takes in one move
(`gerber2ldi/backends/gcode.py` is the writer that relies on this).

The job FRAME (``Job.pass_start_x``/``Job.pass_end_x``) is a different thing on purpose:
it is the figure edge plus the strip, and the table check and the placement are computed
from it — hence trimming a row never moves the figure, and the same layer with the same
``--place`` still lands in the same machine coordinates.

Everything here is module level, and that is the point: the exposed run and the duration
are counted BEFORE the job exists (``build_job`` puts them into the ``.nc`` header and the
CLI prints them), and the count must come from the SAME passes the writer writes — not
from a second opinion about them. ``Job`` keeps thin methods (``Job.row_pass``,
``Job.pass_lengths``) that delegate here, so no caller has to know which module the rule
lives in.

Lifted out of ``job/job.py`` while С2 of `plan_2026_10_23_dose_by_speed` was about to push
that file past 600 lines. Behaviour did not change: the code and its docstrings moved as
they were, and the whole suite was green before and after.
"""
from __future__ import annotations

from ..scan import ScanRow

Box = tuple[float, float, float, float]
"""``(x0, y0, x1, y1)`` in machine millimetres — the job frame the table check uses."""


def row_pass(row: ScanRow, overscan_mm: float) -> tuple[float, float] | None:
    """The pass rule itself: the row's own copper plus the strip, or nothing at all.

    The strip before the first segment and after the last one is the run-up: the laser
    must already be at full speed on the first lit pixel and keep it past the last one
    (design §4.2). Everything beyond that strip is travel over empty table — the job used
    to cross the whole frame on every row, so a row with one pad at the left edge drove
    the full width of the board and came back (Денис 22.10: the exposure took as long as
    the pocket in GGEasy).
    """
    if not row.segments:
        return None
    return (row.segments[0][0] - overscan_mm,
            row.segments[-1][1] + overscan_mm)


def pass_lengths(rows: tuple[ScanRow, ...], overscan_mm: float,
                 box: Box) -> tuple[float, float]:
    """The run the head really travels, and the run it would travel at full width.

    ``exposed`` sums the passes of the rows that have copper, run-ups included; a row with
    nothing on it costs nothing, which is what the trimming bought. ``full_width`` is what
    the SAME rows would cost if each of them crossed the whole job frame — the number the
    operator compares against.
    """
    exposed = 0.0
    for row in rows:
        span = row_pass(row, overscan_mm)
        if span is not None:
            exposed += span[1] - span[0]
    frame = (box[2] + overscan_mm) - (box[0] - overscan_mm)
    return exposed, len(rows) * frame


def duration_parts(seconds: float) -> tuple[int, int]:
    """(minutes, seconds) of a duration, whole seconds rounded DOWN — the line says ``>=``.

    A fraction of a second would be a claim about a speed the machine was never measured
    at; whole seconds below the estimate keep the promise true. The SPLIT lives here so
    that the ASCII header and the translated stdout line cannot disagree about it — only
    the words of the units differ (`format_duration` below, `cli._duration`).
    """
    return divmod(int(seconds), 60)


def format_duration(seconds: float) -> str:
    """ASCII ``4 min 06 s`` (or ``48 s``) for the ``.nc`` header, which is never translated."""
    minutes, rest = duration_parts(seconds)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"
