# gerber2ldi/backends/gcode.py
"""GRBL 1.1 G-code in laser mode (design §12, plan Ш4).

The shape of a pass, per row — the M4 IS THE POINT:

    M5                          laser off before the rapid
    G0 X<run-up start> Y<row>   rapid to the strip in front of THIS row's copper
    M4 S0                       laser MODE ON at zero power
    G1 X<x0> S0                 the run-up: exactly overscan_mm to the first segment
    G1 X<x1> S<s>               draw at working power
    ...                         (S0 travel between segments when needed)
    G1 X<run-up end> S0         run out past the last segment, constant speed

The pass belongs to the ROW, not to the job frame (``Job.row_pass``): it starts one
``overscan_mm`` before the row's first lit pixel and ends one ``overscan_mm`` after its
last one. The job frame (``pass_start_x``/``pass_end_x``) is what the table check and the
placement are computed from, so trimming never moves the figure. A row with no copper at
all gets NO pass — no rapid, no ``M4``: the head stays where the previous row left it, and
the next ``G0`` is a diagonal, which GRBL takes in one move. Every pass runs in +X, so the
run-up takes up the belt slack in the same direction on every row.

Without `M4` after `M5`, a `G1 … S<s>` does NOT switch the laser on: `M5` leaves
laser mode, and GRBL then ignores the S word. The first generated program had one
`M4` in the header and an `M5` per row, i.e. a laser that never fires — found by
Денис on the machine, 20.10.

`M4` is dynamic power: the S value follows the speed, and with `$32=1` GRBL does
not stop on every S change. The header therefore carries
`; requires GRBL laser mode $32=1` — without that setting the machine stutters at
each S change.

`F` is millimetres per MINUTE in GRBL; the conversion from mm/s lives in
``job.job.feed_mm_per_min`` and nowhere else. The header carries the profile's own feed,
and a row whose speed differs gets its ``F`` on the FIRST ``G1`` of that row — the run-up,
where the head accelerates. Only on a change: GRBL keeps ``F`` modal, so repeating it every
row would grow the file and change nothing. That is how a dose strip by SPEED works when
the laser is on/off and the power word cannot carry the dose.

`S` is written PER ROW from ``Job.row_s_values``: a dose strip exposes each band of
rows with its own power, so one number for the whole program would under- or
over-expose every band but one.

Header notes come from ``Job.notes`` and are ASCII English on purpose (the file is
written with ``encoding="ascii"``); :func:`_comment` refuses anything else instead of
letting a translation reach the machine as question marks.

`read_segments()` parses the subset we write, for `preview <nc>`. It tracks the
laser state (`M4`/`M5`), because a parser that ignores it would see copper where
the machine sees nothing.
"""
from __future__ import annotations

from pathlib import Path

from ..errors import JobError
from ..i18n import _
from ..scan import ScanRow

NUMBER_DECIMALS = 3
LASER_MODE_COMMENT = "; requires GRBL laser mode $32=1"
WORK_ZERO_COMMENT = ("; work coordinates G54: the operator sets the work zero at the "
                     "table corner named by profile.table.origin (front-left)")
"""Coordinates here are WORK coordinates. Without the operator setting G54 to the
same corner the "inside the table" check means nothing — it compares numbers to a
frame nobody established."""


def _comment(text: str) -> str:
    """A header note as a G-code comment; refuses non-ASCII (see the docstring)."""
    if not text.isascii():
        raise JobError(_("A note for the G-code header is not ASCII: {text}")
                       .format(text=text))
    return f"; {text}"


def gc_number(value: float) -> str:
    """Fixed 0.001 mm precision, no trailing zeros, no negative zero."""
    text = f"{value:.{NUMBER_DECIMALS}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def render_gcode(job) -> list[str]:
    """The whole program as lines — separated from writing so tests can diff it."""
    lines = [LASER_MODE_COMMENT, WORK_ZERO_COMMENT]
    lines.extend(_comment(note) for note in job.notes)
    lines.extend(["G21", "G90", f"F{gc_number(job.feed_mm_per_min)}"])
    feed = job.feed_mm_per_min
    for row, s_value, row_feed in zip(job.rows, job.row_s_values,
                                      job.row_feeds_mm_per_min):
        span = job.row_pass(row)
        if span is None:
            continue                    # nothing to expose on this row: no travel at all
        pass_start, pass_end = span
        lines.append("M5")
        lines.append(f"G0 X{gc_number(pass_start)} Y{gc_number(row.y_mm)}")
        lines.append("M4 S0")
        # The row's moves first, so that the feed word can land on the FIRST of them
        # whatever it is — the run-up normally, the drawing segment when overscan is 0.
        moves: list[tuple[float, int]] = []
        position = pass_start
        for start, end in row.segments:
            if start > position:
                moves.append((start, 0))
            moves.append((end, s_value))
            position = end
        if pass_end > position:
            moves.append((pass_end, 0))
        for index, (target, power_word) in enumerate(moves):
            suffix = ""
            if index == 0 and row_feed != feed:
                suffix = f" F{gc_number(row_feed)}"
                feed = row_feed
            lines.append(f"G1 X{gc_number(target)} S{power_word}{suffix}")
    lines.append("M5")
    return lines


def write_gcode(job, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(render_gcode(job)) + "\n", encoding="ascii")
    return target


def read_segments(path: str | Path) -> tuple[ScanRow, ...]:
    """Parse a program we wrote back into rows of "laser on" segments.

    The laser is ON only while the machine is in laser mode (`M4`, switched off by
    `M5`) AND the power word is above zero — exactly the state machine GRBL runs.
    Anything the writer does not emit is ignored on purpose: a preview must never
    pretend to understand foreign G-code.
    """
    rows: dict[float, list[tuple[float, float]]] = {}
    x = 0.0
    y = 0.0
    power = 0.0
    laser_mode = False
    for raw in Path(path).read_text(encoding="ascii").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        command = words[0]
        numbers = {}
        for word in words[1:]:
            if word[:1] in ("X", "Y", "S") and len(word) > 1:
                try:
                    numbers[word[0]] = float(word[1:])
                except ValueError:
                    pass
        if command == "M4":
            laser_mode = True
            power = numbers.get("S", power)
        elif command == "M5":
            laser_mode = False
            power = 0.0
        elif command == "G0":
            x = numbers.get("X", x)
            y = numbers.get("Y", y)
            rows.setdefault(round(y, NUMBER_DECIMALS), [])
        elif command == "G1":
            target_x = numbers.get("X", x)
            if "S" in numbers:
                power = numbers["S"]
            if laser_mode and power > 0.0 and target_x != x:
                rows.setdefault(round(y, NUMBER_DECIMALS), []).append(
                    (min(x, target_x), max(x, target_x)))
            x = target_x
    return tuple(ScanRow(height, tuple(segments)) for height, segments in sorted(rows.items()))
