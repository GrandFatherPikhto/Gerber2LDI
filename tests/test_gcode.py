"""The G-code writer, checked by an INDEPENDENT parser (plan Ш4, main cell).

The parser below is written here on purpose and shares no code with
`gerber2ldi.backends.gcode`: the main cell compares the writer against a second
implementation, not against itself.

Cells:

    обратный разбор          отрезки из .nc = отрезки задания, допуск 0.001 мм
    контроль                 заведомо испорченный .nc (сдвинутый на 0.5 мм отрезок)
                             обязан провалить ту же проверку
    M5                       перед каждым G0 и последней строкой файла
    S                        никогда больше usable_fraction · s_max
    числа                    0.001 мм, без хвостовых нулей, без «-0»
"""
import pytest

from gerber2ldi.backends.gcode import gc_number, render_gcode, write_gcode
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


def _parse(text: str) -> dict[float, list[tuple[float, float]]]:
    """Second implementation: G0 sets the row, G1 with S>0 draws a segment."""
    rows: dict[float, list[tuple[float, float]]] = {}
    x = y = 0.0
    for line in text.splitlines():
        words = line.split()
        if not words:
            continue
        if words[0] == "G0":
            for word in words[1:]:
                if word.startswith("X"):
                    x = float(word[1:])
                elif word.startswith("Y"):
                    y = float(word[1:])
            rows.setdefault(round(y, 3), [])
        elif words[0] == "G1":
            end, power = x, 0.0
            for word in words[1:]:
                if word.startswith("X"):
                    end = float(word[1:])
                elif word.startswith("S"):
                    power = float(word[1:])
            if power > 0.0 and end != x:
                rows.setdefault(round(y, 3), []).append((min(x, end), max(x, end)))
            x = end
    return rows


def _assert_rows_match(parsed, job, tolerance: float = TOLERANCE_MM) -> None:
    for row in job.rows:
        expected = row.segments
        got = tuple(parsed.get(round(row.y_mm, 3), ()))
        assert len(got) == len(expected), (row.y_mm, "segments", got, expected)
        for (got_start, got_end), (want_start, want_end) in zip(got, expected):
            assert abs(got_start - want_start) <= tolerance
            assert abs(got_end - want_end) <= tolerance


def _corrupt_first_drawn_segment(text: str) -> str:
    """The control sample: move one drawing move by 0.5 mm, leave the rest alone."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        words = line.split()
        if words and words[0] == "G1" and any(w.startswith("S") and w != "S0" for w in words):
            for position, word in enumerate(words):
                if word.startswith("X"):
                    words[position] = f"X{float(word[1:]) + 0.5:.3f}"
                    lines[index] = " ".join(words)
                    return "\n".join(lines)
    raise AssertionError("there is no drawing move to corrupt — the writer wrote nothing")


def test_gcode_round_trips_into_the_job_rows(tmp_path, make_machine):
    machine = make_machine()
    job = build_job(_layer(tmp_path), machine, power=0.5, spot_compensation=False)
    target = write_gcode(job, tmp_path / "layer.nc")
    parsed = _parse(target.read_text())
    assert parsed, "the parser found no rows at all"
    _assert_rows_match(parsed, job)


def test_a_corrupted_program_fails_the_same_check(tmp_path, make_machine):
    """Control: the check must react, or it proves nothing (rules п.30)."""
    machine = make_machine()
    job = build_job(_layer(tmp_path), machine, power=0.5, spot_compensation=False)
    good = render_gcode(job)
    text = "\n".join(good)
    corrupted = _corrupt_first_drawn_segment(text)
    assert corrupted != text
    with pytest.raises(AssertionError):
        _assert_rows_match(_parse(corrupted), job)


def test_laser_is_off_before_every_rapid_and_at_the_end(tmp_path, make_machine):
    machine = make_machine()
    job = build_job(_layer(tmp_path), machine, power=0.5, spot_compensation=False)
    lines = render_gcode(job)
    assert lines[-1] == "M5", "the program must end with the laser off"
    rapid_positions = [i for i, line in enumerate(lines) if line.startswith("G0 ")]
    assert rapid_positions, "no rapid moves at all"
    for position in rapid_positions:
        assert lines[position - 1] == "M5", f"line {position}: G0 without M5 in front"


def test_s_value_never_exceeds_the_ceiling(tmp_path, make_machine):
    machine = make_machine()
    ceiling = int(round(machine.laser.usable_fraction * machine.laser.s_max))
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
], ids=["integer", "half", "zero", "negative zero", "round down", "round up", "negative"])
def test_numbers_have_micrometre_precision_without_trailing_zeros(value, expected):
    assert gc_number(value) == expected
