"""The G-code writer, checked by an INDEPENDENT parser (plan Ш4, main cell).

The parser below is written here on purpose and shares no code with
`gerber2ldi.backends.gcode`. It also runs GRBL's laser state machine: the laser is
ON only while `M4` is in force and the power word is above zero. The first version
of both the writer and this parser ignored `M4`/`M5`, so the two implementations
agreed on a program the machine would never fire — that is the trap this file now
closes.

Cells:

    обратный разбор          отрезки из .nc = отрезки задания, допуск 0.001 мм
    контроль                 программа БЕЗ M4 после G0 даёт ноль отрезков и обязана
                             провалить обратный разбор
    M4                       после каждого G0, до первого G1 с S>0; ни одного G0
                             при включённом лазере
    F                        мм/мин: 50 мм/с → F3000
    шапка                    комментарий про $32=1
    S                        никогда больше usable_fraction · s_max
    числа                    0.001 мм, без хвостовых нулей, без «-0»
"""
import pytest

from gerber2ldi.backends.gcode import (LASER_MODE_COMMENT, WORK_ZERO_COMMENT,
                                       gc_number, render_gcode, write_gcode)
from gerber2ldi.job import build_job
from gerber2ldi.source import load_layer

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
FOOTER = "M02*\n"
TWO_STRIPS = (HEADER + "%ADD10R,2.000000X2.000000*%\n%ADD11R,4.000000X2.000000*%\n"
              "%LPD*%\nG01*\nD10*\nX1000000Y1000000D03*\nD11*\n"
              "X7000000Y1000000D03*\n" + FOOTER)
TOLERANCE_MM = 0.001


def _layer(tmp_path):
    path = tmp_path / "layer.gbr"
    path.write_text(TWO_STRIPS)
    return load_layer(path)


def _job(tmp_path, make_machine):
    return build_job(_layer(tmp_path), make_machine(), power=0.5,
                     spot_compensation=False)


def _parse(text: str) -> dict[float, list[tuple[float, float]]]:
    """Second implementation, with the laser state GRBL actually runs."""
    rows: dict[float, list[tuple[float, float]]] = {}
    x = y = 0.0
    power = 0.0
    laser_mode = False
    for line in text.splitlines():
        words = line.split(";", 1)[0].split()
        if not words:
            continue
        command = words[0]
        numbers = {w[0]: float(w[1:]) for w in words[1:] if w[:1] in "XYS"}
        if command == "M4":
            laser_mode = True
            power = numbers.get("S", power)
            continue
        if command == "M5":
            laser_mode = False
            power = 0.0
            continue
        if command == "G0":
            x, y = numbers.get("X", x), numbers.get("Y", y)
            rows.setdefault(round(y, 3), [])
        elif command == "G1":
            target_x = numbers.get("X", x)
            power = numbers.get("S", power)
            if laser_mode and power > 0.0 and target_x != x:
                rows.setdefault(round(y, 3), []).append((min(x, target_x), max(x, target_x)))
            x = target_x
    return rows


def _assert_rows_match(parsed, job, tolerance: float = TOLERANCE_MM) -> None:
    for row in job.rows:
        expected = row.segments
        got = tuple(parsed.get(round(row.y_mm, 3), ()))
        assert len(got) == len(expected), (row.y_mm, "segments", got, expected)
        for (got_start, got_end), (want_start, want_end) in zip(got, expected):
            assert abs(got_start - want_start) <= tolerance
            assert abs(got_end - want_end) <= tolerance


def _strip_m4(text: str) -> str:
    """The control sample: remove every M4, i.e. the laser mode never turns on."""
    return "\n".join(line for line in text.splitlines() if line.strip() != "M4 S0")


def test_gcode_round_trips_into_the_job_rows(tmp_path, make_machine):
    job = _job(tmp_path, make_machine)
    target = write_gcode(job, tmp_path / "layer.nc")
    parsed = _parse(target.read_text())
    assert parsed, "the parser found no rows at all"
    _assert_rows_match(parsed, job)


def test_program_without_m4_makes_no_segments_and_fails_the_check(tmp_path, make_machine):
    """Control: the laser needs M4 after every M5, or the machine draws nothing.

    This is the defect Денис found on the machine, so the check must see it: with
    the M4 lines removed the program is syntactically fine and lights nothing.
    """
    job = _job(tmp_path, make_machine)
    text = "\n".join(render_gcode(job))
    broken = _strip_m4(text)
    assert "M4" not in broken
    assert _parse(broken) == {} or not any(_parse(broken).values())
    with pytest.raises(AssertionError):
        _assert_rows_match(_parse(broken), job)


def test_laser_mode_is_switched_on_after_every_rapid(tmp_path, make_machine):
    """M5 → G0 → M4 S0, and no G0 while the laser is on."""
    lines = render_gcode(_job(tmp_path, make_machine))
    laser_on = False
    for index, line in enumerate(lines):
        if line.startswith("M4"):
            laser_on = True
        elif line == "M5":
            laser_on = False
        elif line.startswith("G0"):
            assert not laser_on, f"line {index}: rapid with the laser on"
            assert lines[index - 1] == "M5", f"line {index}: rapid without M5 first"
            assert lines[index + 1] == "M4 S0", f"line {index}: no M4 after the rapid"
    assert lines[-1] == "M5", "the program must end with the laser off"


def test_feed_is_millimetres_per_minute(tmp_path, make_machine):
    """GRBL's F is mm/min: 50 mm/s must be F3000, not F50 (sixty times the dose)."""
    job = _job(tmp_path, make_machine)
    assert job.feed_mm_per_min == pytest.approx(3000.0)
    lines = render_gcode(job)
    assert "F3000" in lines
    assert "F50" not in lines


def test_header_asks_for_grbl_laser_mode(tmp_path, make_machine):
    """Without $32=1 GRBL stops on every S change and the edges come out stepped."""
    lines = render_gcode(_job(tmp_path, make_machine))
    assert lines[0] == LASER_MODE_COMMENT
    assert "32" in lines[0] and "laser" in lines[0]


def test_header_states_the_work_coordinate_frame(tmp_path, make_machine):
    """Numbers are G54 work coordinates; the operator must zero the same corner."""
    lines = render_gcode(_job(tmp_path, make_machine))
    assert lines[1] == WORK_ZERO_COMMENT
    assert "G54" in lines[1] and "front-left" in lines[1]


def test_s_value_never_exceeds_the_ceiling(tmp_path, make_machine):
    machine = make_machine()
    ceiling = int(machine.laser.usable_fraction * machine.laser.s_max)
    job = build_job(_layer(tmp_path), machine, power=0.8, spot_compensation=False)
    powers = {int(word[1:]) for line in render_gcode(job)
              for word in line.split() if word.startswith("S") and word != "S0"}
    assert powers == {ceiling} == {job.s_value}


@pytest.mark.parametrize("value, expected", [
    (10.0, "10"),
    (0.5, "0.5"),
    (0.0, "0"),
    (-0.0, "0"),
    (1.2344, "1.234"),
    (1.2346, "1.235"),
    (-2.5, "-2.5"),
], ids=["integer", "half", "zero", "negative zero", "round down", "round up",
        "negative"])
def test_numbers_have_micrometre_precision_without_trailing_zeros(value, expected):
    assert gc_number(value) == expected
