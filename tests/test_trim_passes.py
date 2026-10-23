"""A row's pass belongs to the ROW, not to the job frame (plan `plan_2026_10_22_trim_passes`, Т1).

The job used to send every row from the frame's left edge to its right one, so a row with
one pad in the corner drove the whole width of the board and came back — the exposure took
as long as the pocket in GGEasy (Денис 22.10). Now a row starts one `overscan_mm` before
its OWN first lit pixel and ends one `overscan_mm` after its last one, always in +X, so the
run-up takes up the belt slack in the same direction on every row.

The job FRAME did not move: `pass_start_x`/`pass_end_x` are still the figure edge plus the
strip, the table check and the placement are computed from it, and therefore the same
layer with the same `--place` still lands in the same machine coordinates.

Cells:

    один отрезок            G0 X(a − overscan), затем G1 X(b + overscan) S0 последним
    две строки              у каждой своё начало: строка не едет через всю плату
    разгон                  перед первым S>0 и после последнего — ровно overscan по +X
    пустая строка           прохода нет вовсе: ни G0, ни M4
    во всю ширину           проход = рамке задания (обрезать нечего)
    внутри рамки            плата с --frame, полоска дозы, сетка: проход в [pass_start_x, pass_end_x]
    постановка              тот же --place — те же координаты станка (числа посчитаны руками)
    превью                  read_segments(.nc) = отрезки задания

The numbers are hand-computed for the fixture machine (50 step/mm, row 20 um, overscan
5 mm, speed 50 mm/s, S500 at power 0.5), so a cell that goes green for the wrong reason is
visible in the arithmetic.
"""
import pytest

from gerber2ldi.backends.gcode import read_segments, render_gcode, write_gcode
from gerber2ldi.job import build_job
from gerber2ldi.patterns import dose, grid
from gerber2ldi.source import load_layer

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
FOOTER = "M02*\n"
NO_SPOT = "[calibration]\nscale_x = 1.0\nscale_y = 1.0\nskew_deg = 0.0\n"


def _gerber(pads, size=(2.0, 2.0)) -> str:
    """RS-274X with rectangles of ``size`` mm at the lower-left corners listed in ``pads``."""
    width, height = size
    body = "".join(f"X{int(round(x * 1e6))}Y{int(round(y * 1e6))}D03*\n" for x, y in pads)
    return (HEADER + f"%ADD10R,{width:.6f}X{height:.6f}*%\n%LPD*%\nG01*\nD10*\n"
            + body + FOOTER)


def _layer(tmp_path, pads, size=(2.0, 2.0), name="layer.gbr"):
    path = tmp_path / name
    path.write_text(_gerber(pads, size))
    return load_layer(path)


def _job(tmp_path, make_machine, pads, size=(2.0, 2.0), name="layer.gbr", **kwargs):
    layer = _layer(tmp_path, pads, size, name)
    return build_job(layer, make_machine(calibration_text=NO_SPOT), power=0.5,
                     spot_compensation=False, **kwargs)


def _passes(lines) -> list[dict]:
    """Every pass of the program: the rapid's X and Y, then its ``G1`` moves in order."""
    passes: list[dict] = []
    for line in lines:
        if not line or line.startswith(";"):
            continue
        words = line.split()
        command = words[0]
        numbers = {word[0]: float(word[1:]) for word in words[1:] if word[:1] in "XYS"}
        if command == "G0":
            passes.append({"start": numbers["X"], "y": numbers["Y"], "moves": []})
        elif command == "G1":
            assert passes, "a G1 before any G0"
            passes[-1]["moves"].append((numbers["X"], numbers.get("S", 0.0)))
    return passes


def test_a_row_runs_from_its_own_copper_minus_the_run_up(tmp_path, make_machine):
    """A 2x2 pad flashed at (1, 1) covers 0 … 2 mm; the placement puts the run-up at 0.

    So the copper runs 5 … 7 and the pass of every row is 0 … 12: rapid to 0, run-up to
    5, draw to 7, run out to 12.
    """
    job = _job(tmp_path, make_machine, [(1.0, 1.0)])
    passes = _passes(render_gcode(job))
    assert passes, "the program has no pass at all"
    for one in passes:
        assert one["start"] == pytest.approx(0.0), "the rapid goes to copper − overscan"
        assert one["moves"] == [(5.0, 0.0), (7.0, 500.0), (12.0, 0.0)], (
            "run-up S0, the lit segment, the run-out S0")


def test_each_row_starts_where_its_own_copper_starts(tmp_path, make_machine):
    """A row with copper at the left edge and a row with copper far right start differently.

    The old writer sent both from the frame's left edge: the first row of the right pad
    crossed the empty middle of the board for nothing.
    """
    job = _job(tmp_path, make_machine, [(1.0, 1.0), (20.0, 5.0)])
    starts = sorted({one["start"] for one in _passes(render_gcode(job))})
    assert starts == [pytest.approx(0.0), pytest.approx(19.0)]


def test_every_row_has_a_run_up_of_exactly_the_overscan(tmp_path, make_machine):
    """Before the first lit pixel and after the last one: exactly the strip, no more.

    This is the guard of the run-up AND of the belt slack: the laser must be at full speed
    where the exposure starts, and the run-up always lies in +X.
    """
    job = _job(tmp_path, make_machine, [(1.0, 1.0), (20.0, 5.0)])
    overscan = job.overscan_mm
    passes = _passes(render_gcode(job))
    assert len(passes) == job.inked_rows, "one pass per row that has copper"
    for one in passes:
        moves = one["moves"]
        lit = [index for index, (_, power) in enumerate(moves) if power > 0.0]
        assert lit, "a pass with no lit move"
        assert lit[0] >= 1, "no room for a run-up"
        assert lit[-1] + 1 < len(moves), "no room for a run-out"
        run_up, run_out = moves[lit[0] - 1], moves[lit[-1] + 1]
        assert run_up[1] == 0.0 and run_out[1] == 0.0
        assert run_up[0] - one["start"] == pytest.approx(overscan), "the run-up is the strip"
        assert run_out[0] - moves[lit[-1]][0] == pytest.approx(overscan), "the run-out too"


def test_an_empty_row_gets_no_pass_at_all(tmp_path, make_machine):
    """Nothing to expose on a row means no rapid and no M4: the head stays where it was."""
    job = _job(tmp_path, make_machine, [(1.0, 1.0), (1.0, 20.0)])
    passes = _passes(render_gcode(job))
    assert len(job.rows) > job.inked_rows, "the pads must leave empty rows between them"
    assert len(passes) == job.inked_rows
    ys = {round(one["y"], 3) for one in passes}
    assert not any(9.0 < y < 15.0 for y in ys), "no pass in the gap between the pads"


def test_a_full_width_row_keeps_the_job_frame(tmp_path, make_machine):
    """When every row spans the figure, trimming has nothing to cut: pass == frame."""
    job = _job(tmp_path, make_machine, [(0.0, 0.0)], size=(10.0, 2.0))
    for row in job.rows:
        assert job.row_pass(row) == pytest.approx((job.pass_start_x, job.pass_end_x))


TRIM_FRAME_CASES = ["board with a frame", "dose strip", "grid"]


@pytest.mark.parametrize("case", TRIM_FRAME_CASES, ids=["frame", "dose", "grid"])
def test_every_pass_stays_inside_the_job_frame(tmp_path, make_machine, case):
    """A trimmed pass is inside the frame by construction — the table check still holds."""
    machine = make_machine(calibration_text=NO_SPOT)
    if case == "frame":
        copper = _layer(tmp_path, [(5.0, 5.0)], name="copper.gbr")
        frame = _layer(tmp_path, [(0.0, 0.0)], size=(40.0, 30.0), name="frame.gbr")
        job = build_job(copper, machine, power=0.5, spot_compensation=False, frame=frame)
    elif case == "dose":
        built = dose(machine, (0.02, 0.05))
        job = build_job(built.layer, built.machine, power_bands=built.bands,
                        notes=built.notes, board=False, spot_compensation=False)
    else:
        built = grid(machine, width_mm=4.0, height_mm=3.0, line_mm=0.2, gap_mm=0.3)
        job = build_job(built.layer, built.machine, power=0.5, notes=built.notes,
                        board=False, spot_compensation=False)
    spans = [job.row_pass(row) for row in job.rows]
    assert any(span is not None for span in spans)
    for span in spans:
        if span is None:
            continue
        assert job.pass_start_x <= span[0] <= span[1] <= job.pass_end_x


def test_the_same_place_puts_the_copper_in_the_same_machine_coordinates(tmp_path,
                                                                       make_machine):
    """`--place` and the frame are untouched: only the idle part of a row moved.

    Hand-computed for the fixture. A 2x2 pad flashed at (1, 1) covers 0 … 2 mm; the
    placement works from the ink box of the ROWS (the first row centre is 0.01), so
    ``--place 3,2`` gives shift x = 3 − (0 − 5) = 8 and shift y = 2 − 0.01 = 1.99: copper
    8 … 10 mm, rows 2.00 … 3.98, the job frame 3 … 15 and the pass of these rows 3 … 15.
    """
    job = _job(tmp_path, make_machine, [(1.0, 1.0)], place_mm=(3.0, 2.0))
    inked = [row for row in job.rows if row.segments]
    assert {segments for row in inked for segments in row.segments} == {(8.0, 10.0)}
    assert inked[0].y_mm == pytest.approx(2.0)
    assert inked[-1].y_mm == pytest.approx(3.98)
    assert job.pass_start_x == pytest.approx(3.0), "the frame did not move"
    assert job.pass_end_x == pytest.approx(15.0)
    first = _passes(render_gcode(job))[0]
    assert first["start"] == pytest.approx(3.0)
    assert first["moves"][-1] == pytest.approx((15.0, 0.0))


def test_the_preview_reads_back_the_job_rows(tmp_path, make_machine):
    """A trimmed program is still the same drawing: the reverse parser sees job.rows."""
    job = _job(tmp_path, make_machine, [(1.0, 1.0), (20.0, 5.0)])
    target = write_gcode(job, tmp_path / "trim.nc")
    expected = {round(row.y_mm, 3): row.segments for row in job.rows if row.segments}
    got = {row.y_mm: row.segments for row in read_segments(target)}
    assert got == expected
