# gerber2ldi/backends/gcode.py
"""GRBL 1.1 G-code, written conservatively (design §12, plan Ш4).

The shape of a pass, per row:

    M5                          laser off BEFORE any rapid (rules: hard contract)
    G0 X<run-up start> Y<row>   rapid to the strip in front of the figure
    G1 X<x0> S0                 travel to the first segment with the laser off
    G1 X<x1> S<s>               draw the segment at working power
    ...                         (S0 travel between segments when needed)
    G1 X<run-up end> S0         run out BEYOND the figure, still at constant speed

`S` is a fraction of `s_max` and can never exceed `usable_fraction · s_max` — the
ceiling is enforced in `job.py` by refusing, so this module only formats.

Numbers: millimetres with three decimals, trailing zeros trimmed, and never `-0`.

`read_segments()` parses back the file we wrote. It exists for `preview <nc>`; the
tests deliberately carry their OWN parser, so the main cell compares two
independent implementations instead of the writer with itself.
"""
from __future__ import annotations

from pathlib import Path

from ..scan import ScanRow

NUMBER_DECIMALS = 3


def gc_number(value: float) -> str:
    """Fixed 0.001 mm precision, no trailing zeros, no negative zero."""
    text = f"{value:.{NUMBER_DECIMALS}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def render_gcode(job) -> list[str]:
    """The whole program as lines — separated from writing so tests can diff it."""
    lines = ["G21", "G90", "M4", f"F{gc_number(job.speed_mm_s)}"]
    for row in job.rows:
        lines.append("M5")
        lines.append(f"G0 X{gc_number(job.pass_start_x)} Y{gc_number(row.y_mm)}")
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

    Only the subset this writer emits is understood; anything else is ignored on
    purpose, because a preview must never pretend to understand foreign G-code.
    """
    rows: dict[float, list[tuple[float, float]]] = {}
    x = 0.0
    y = 0.0
    for raw in Path(path).read_text(encoding="ascii").splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        command = words[0]
        if command == "G0":
            for word in words[1:]:
                if word.startswith("X"):
                    x = float(word[1:])
                elif word.startswith("Y"):
                    y = float(word[1:])
            rows.setdefault(round(y, NUMBER_DECIMALS), [])
        elif command == "G1":
            target_x = x
            power = 0
            for word in words[1:]:
                if word.startswith("X"):
                    target_x = float(word[1:])
                elif word.startswith("S"):
                    power = float(word[1:])
            if power > 0 and target_x != x:
                rows.setdefault(round(y, NUMBER_DECIMALS), []).append(
                    (min(x, target_x), max(x, target_x)))
            x = target_x
    return tuple(ScanRow(height, tuple(segments)) for height, segments in sorted(rows.items()))
