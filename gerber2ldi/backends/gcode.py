# gerber2ldi/backends/gcode.py
"""GRBL 1.1 G-code in laser mode (design §12, plan Ш4).

The shape of a pass, per row — the M4 IS THE POINT:

    M5                          laser off before the rapid
    G0 X<run-up start> Y<row>   rapid to the strip in front of the figure
    M4 S0                       laser MODE ON at zero power
    G1 X<x0> S0                 travel to the first segment
    G1 X<x1> S<s>               draw at working power
    ...                         (S0 travel between segments when needed)
    G1 X<run-up end> S0         run out past the figure, constant speed

Without `M4` after `M5`, a `G1 … S<s>` does NOT switch the laser on: `M5` leaves
laser mode, and GRBL then ignores the S word. The first generated program had one
`M4` in the header and an `M5` per row, i.e. a laser that never fires — found by
Денис on the machine, 20.10.

`M4` is dynamic power: the S value follows the speed, and with `$32=1` GRBL does
not stop on every S change. The header therefore carries
`; requires GRBL laser mode $32=1` — without that setting the machine stutters at
each S change.

`F` is millimetres per MINUTE in GRBL; the conversion from mm/s lives in
``Job.feed_mm_per_min`` and nowhere else.

`read_segments()` parses the subset we write, for `preview <nc>`. It tracks the
laser state (`M4`/`M5`), because a parser that ignores it would see copper where
the machine sees nothing.
"""
from __future__ import annotations

from pathlib import Path

from ..scan import ScanRow

NUMBER_DECIMALS = 3
LASER_MODE_COMMENT = "; requires GRBL laser mode $32=1"


def gc_number(value: float) -> str:
    """Fixed 0.001 mm precision, no trailing zeros, no negative zero."""
    text = f"{value:.{NUMBER_DECIMALS}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def render_gcode(job) -> list[str]:
    """The whole program as lines — separated from writing so tests can diff it."""
    lines = [LASER_MODE_COMMENT, "G21", "G90",
             f"F{gc_number(job.feed_mm_per_min)}"]
    for row in job.rows:
        lines.append("M5")
        lines.append(f"G0 X{gc_number(job.pass_start_x)} Y{gc_number(row.y_mm)}")
        lines.append("M4 S0")
        position = job.pass_start_x
        for start, end in row.segments:
            if start > position:
                lines.append(f"G1 X{gc_number(start)} S0")
            lines.append(f"G1 X{gc_number(end)} S{job.s_value}")
            position = end
        if job.pass_end_x > position:
            lines.append(f"G1 X{gc_number(job.pass_end_x)} S0")
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
