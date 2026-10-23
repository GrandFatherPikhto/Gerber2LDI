"""Calibration patterns as DATA: rectangles, bands, per-row power (plan Ш5).

Cells:

    shapes          прямоугольник → Shape с точным bbox, Layer собирает рамку
    bars            порядок групп, промежуток = ширине, обе ориентации, 60 фигур
    bars без пятна  строится, шапка говорит «spot compensation: off»
    bars ширина     меньше шага строки → предупреждение, а не тишина
    dose            S на строку: три уровня, границы полос на строках
    dose уровень    выше потолка → отказ (не подрезка), полоса без строки → отказ
    dose полосы     пересечение, power вместе с bands, ни одного — отказы
    grid влезает    целых линий в отрезок: ровно по краю, на 1 мкм короче, остаток
    grid направление x / y / both: сколько фигур, какая сторона у линии
    grid период     начала линий через период = линия + промежуток
    grid ширина     в .nc (обратный разбор): 0.2 в промежутке, 4.0 в полосе строк
    grid кратность  0.21/0.25 при шаге 0.02 — два предупреждения; 0.2/0.3 — тишина
    grid отказы     линия 0, промежуток 0, сторона меньше линии, чужое направление
    Job             s_values неверной длины и не-ASCII заметка — отказы

The pattern is a synthetic `Layer` built from `source.Shape` polygons, so it goes
through the SAME `build_job`: the cells below would catch a second path into the job
as soon as its notes or its per-row power drifted from the Gerber one.
"""
import math

import pytest

from gerber2ldi.backends.gcode import read_segments, render_gcode, write_gcode
from gerber2ldi.errors import JobError
from gerber2ldi.job import Dose, Job, PowerBand, build_job
from gerber2ldi.patterns import (bars, dose, fit_lines, grid, powers_from_series,
                                 speeds_from_series)
from gerber2ldi.patterns.shapes import layer_of, rectangle
from gerber2ldi.scan import ScanRow

NO_SPOT = "[calibration]\nscale_x = 1.0\nscale_y = 1.0\nskew_deg = 0.0\n"


def _bars_layer(machine):
    return bars(machine).layer


def test_rectangle_is_a_shape_with_its_own_frame():
    shape = rectangle(1.0, 2.0, 4.0, 6.0)
    assert shape.dark is True
    assert shape.bbox == (1.0, 2.0, 4.0, 6.0)
    assert len(shape.polygons) == 1
    assert len(shape.polygons[0]) == 4
    with pytest.raises(ValueError):
        rectangle(1.0, 2.0, 1.0, 6.0)


def test_layer_frame_is_the_union_of_its_shapes():
    layer = layer_of((rectangle(0.0, 0.0, 1.0, 1.0), rectangle(5.0, -2.0, 6.0, 0.5)))
    assert layer.bbox == (0.0, -2.0, 6.0, 1.0)
    assert layer.unit == "mm"
    assert layer.aperture_count == 0
    assert layer.has_clear is False


def test_bars_geometry_order_and_gaps(make_machine):
    """Gap = width, five lines per group, horizontal block first, then vertical."""
    pattern = bars(make_machine(calibration_text=NO_SPOT))
    shapes = pattern.layer.shapes
    assert len(shapes) == 2 * 6 * 5, "two blocks, six widths, five lines"
    # Horizontal block: lines along X at the origin, gap = width under each line.
    first, second = shapes[0].bbox, shapes[1].bbox
    assert first == (0.0, 0.0, 20.0, 0.2)
    # Line 0 fills 0 … w, then comes a gap of w, so line 1 starts at 2·w.
    assert second[1] == pytest.approx(first[3] + 0.2), "the gap must equal the width"
    assert second[3] == pytest.approx(0.6)
    # Group pitch = (2·5 − 1 + 3)·w = 12·w: the second group starts at 12·0.2.
    assert shapes[5].bbox[1] == pytest.approx(12 * 0.2)
    # Vertical block starts after the length plus the block gap and runs along Y.
    vertical = shapes[30].bbox
    assert vertical[0] == pytest.approx(22.0)
    assert vertical[2] - vertical[0] == pytest.approx(0.2)
    assert vertical[1] == pytest.approx(0.0)
    assert vertical[3] == pytest.approx(20.0)
    # The frame ends at the last line, so the trailing group gap is not in it:
    # six groups of 12·w wide, minus the 3·w gap after the last one.
    assert pattern.layer.bbox == pytest.approx(
        (0.0, 0.0, 22.0 + 12 * 0.64 - 3 * 0.05, 20.0))


def test_bars_build_without_a_measured_spot_and_say_so(tmp_path, make_machine):
    """A pattern measures the spot, so it must not demand the measurement first."""
    machine = make_machine(calibration_text=NO_SPOT)
    with pytest.raises(Exception, match="Spot radius not measured"):
        build_job(bars(machine).layer, machine, power=0.6)  # compensation on
    pattern = bars(machine)
    job = build_job(pattern.layer, pattern.machine, power=0.6,
                    notes=pattern.notes, spot_compensation=False)
    assert job.inked_rows > 0
    assert any("Spot compensation is off" in warning for warning in job.warnings)
    text = "\n".join(render_gcode(job))
    assert "; spot compensation: off" in text
    assert "; calibration in force: scale_x=1.000000" in text
    assert "test bars" in text


def test_bars_below_the_row_step_are_announced(make_machine):
    pattern = bars(make_machine(calibration_text=NO_SPOT), widths=(0.2, 0.01))
    assert any("0.01" in warning for warning in pattern.warnings)
    assert any("below the row step" in warning for warning in pattern.warnings)


def test_dose_puts_its_power_on_the_rows(tmp_path, make_machine):
    """Three tiles, three levels: every row carries the S of the tile it exposes."""
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = dose(machine, (0.02, 0.05, 0.1))
    assert [band.y0_mm for band in pattern.bands] == [0.0, 10.0, 20.0]
    job = build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                    notes=pattern.notes, spot_compensation=False)
    assert [level.s_value for level in job.doses] == [20, 50, 100]
    assert job.power_fraction is None
    assert len(job.row_s_values) == len(job.rows)
    assert sorted(set(job.row_s_values)) == [20, 50, 100]
    # Boundaries land on row centres: 10 mm / 0.02 = every 500th row.
    assert job.row_s_values[499] == 20
    assert job.row_s_values[500] == 50
    assert job.row_s_values[999] == 50
    assert job.row_s_values[1000] == 100
    text = "\n".join(render_gcode(job))
    assert "; dose 1/3: y 0.000-10.000 mm (figure), power 0.0200, S 20" in text
    assert "; dose 3/3: y 20.000-30.000 mm (figure), power 0.1000, S 100" in text
    target = write_gcode(job, tmp_path / "dose.nc")
    assert {line.split("S")[-1] for line in target.read_text().splitlines()
            if line.startswith("G1") and " S" in line} >= {"0", "20", "50", "100"}


def test_dose_refuses_a_level_above_the_ceiling(make_machine):
    """The ceiling is hard: 0.9 of the module with usable_fraction 0.8 is a refusal."""
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = dose(machine, (0.02, 0.9))
    with pytest.raises(JobError, match="above the ceiling"):
        build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                  spot_compensation=False)


def test_dose_refuses_a_tile_that_covers_no_row(make_machine):
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = dose(machine, (0.02, 0.05), tile_height_mm=0.01)  # row step is 0.02
    with pytest.raises(JobError, match="covers no machine row"):
        build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                  spot_compensation=False)


BANDS = (PowerBand(0.0, 1.0, 0.02),)


@pytest.mark.parametrize("bands, power, message", [
    (BANDS, 0.5, "not both"),
    ((PowerBand(0.0, 2.0, 0.02), PowerBand(1.0, 3.0, 0.05)), None, "overlap"),
    ((PowerBand(1.0, 1.0, 0.02),), None, "is empty"),
    ((PowerBand(0.0, 1.0, 0.0),), None, "above zero"),
], ids=["power and bands", "overlap", "empty band", "zero power"])
def test_job_refuses_bad_power_bands(tmp_path, make_machine, bands, power, message):
    machine = make_machine(calibration_text=NO_SPOT)
    layer = bars(machine).layer
    with pytest.raises(JobError, match=message):
        build_job(layer, machine, power=power, power_bands=bands,
                  spot_compensation=False)


def test_job_requires_some_power(make_machine):
    machine = make_machine(calibration_text=NO_SPOT)
    with pytest.raises(JobError, match="Power is required"):
        build_job(bars(machine).layer, machine, spot_compensation=False)


def test_power_bands_are_refused_with_compensation(make_machine):
    """Compensation adds and removes rows, so a band would stop owning its rows."""
    machine = make_machine()
    pattern = dose(machine, (0.02, 0.05))
    with pytest.raises(JobError, match="cannot be combined with spot compensation"):
        build_job(pattern.layer, pattern.machine, power_bands=pattern.bands)


def test_row_powers_must_match_the_rows(make_machine):
    machine = make_machine(calibration_text=NO_SPOT)
    job = build_job(bars(machine).layer, machine, power=0.5, spot_compensation=False)
    with pytest.raises(JobError, match="row powers"):
        Job(rows=job.rows, box=job.box, overscan_mm=job.overscan_mm,
            speed_mm_s=job.speed_mm_s, s_value=500, power_fraction=0.5,
            side=job.side, process=job.process, s_values=(1, 2))
    assert job.row_s_values == (500,) * len(job.rows)


def test_a_note_that_is_not_ascii_is_refused(make_machine):
    """The .nc is written as ASCII; a translated note must fail loudly, not mojibake."""
    machine = make_machine(calibration_text=NO_SPOT)
    job = build_job(bars(machine).layer, machine, power=0.5, spot_compensation=False,
                    notes=("ширины",))
    with pytest.raises(JobError, match="not ASCII"):
        render_gcode(job)


def test_powers_from_series_is_geometric():
    """Dose acts multiplicatively: equal RATIOS, not equal steps."""
    levels = powers_from_series(0.02, 0.16, 3)
    assert levels[0] == pytest.approx(0.02)
    assert levels[-1] == pytest.approx(0.16)
    assert levels[1] / levels[0] == pytest.approx(levels[2] / levels[1])
    assert levels[1] == pytest.approx(0.02 * math.sqrt(8.0))
    with pytest.raises(ValueError):
        powers_from_series(0.02, 0.16, 1)


def test_dose_rejects_an_empty_level_list(make_machine):
    with pytest.raises(ValueError):
        dose(make_machine(calibration_text=NO_SPOT), ())


# The grid cells all use the same rectangle: 4 x 3 mm, line 0.2, gap 0.3, period 0.5,
# so 8 lines fit across and 6 up, and the numbers read at a glance.
FIT_LINES_CELLS = [
    ((4.0, 0.2, 0.3), 8, "eight lines across a side of four millimetres"),
    ((3.0, 0.2, 0.3), 6, "six lines across a side of three millimetres"),
    ((1.7, 0.2, 0.3), 4, "the last line ends flush with the far edge"),
    ((1.699, 0.2, 0.3), 3, "one micrometre short and the last line does not fit"),
    ((1.8, 0.2, 0.3), 4, "the leftover at the far edge is a gap"),
    ((0.2, 0.2, 0.3), 1, "exactly one line and nothing else"),
    ((0.199, 0.2, 0.3), 0, "no line fits at all"),
]


@pytest.mark.parametrize("args, expected, why", FIT_LINES_CELLS,
                         ids=[cell[2] for cell in FIT_LINES_CELLS])
def test_fit_lines_counts_whole_lines_only(args, expected, why):
    """Г1: a line either fits ENTIRELY or is not drawn — no slivers at the far edge."""
    assert fit_lines(*args) == expected


@pytest.mark.parametrize("direction, along_x, along_y",
                         [("x", 6, 0), ("y", 0, 8), ("both", 6, 8)],
                         ids=["along X", "along Y", "both"])
def test_grid_draws_the_lines_it_is_asked_for(make_machine, direction, along_x, along_y):
    """Г1: `x` — horizontal lines stacked in Y, `y` — vertical stacked in X, `both` — union."""
    pattern = grid(make_machine(calibration_text=NO_SPOT), width_mm=4.0, height_mm=3.0,
                   line_mm=0.2, gap_mm=0.3, direction=direction)
    shapes = pattern.layer.shapes
    horizontal = [shape for shape in shapes
                  if shape.bbox[2] - shape.bbox[0] == pytest.approx(4.0)]
    vertical = [shape for shape in shapes
                if shape.bbox[3] - shape.bbox[1] == pytest.approx(3.0)]
    assert (len(horizontal), len(vertical)) == (along_x, along_y)
    assert all(shape.bbox[3] - shape.bbox[1] == pytest.approx(0.2)
               for shape in horizontal)
    assert all(shape.bbox[2] - shape.bbox[0] == pytest.approx(0.2)
               for shape in vertical)


def test_grid_puts_its_lines_a_period_apart(make_machine):
    """Г1: the period is line + gap, the first line flush with the near edge."""
    pattern = grid(make_machine(calibration_text=NO_SPOT), width_mm=4.0, height_mm=3.0,
                   line_mm=0.2, gap_mm=0.3, direction="y")
    starts = [shape.bbox[0] for shape in pattern.layer.shapes]
    assert starts == [pytest.approx(0.5 * index) for index in range(len(starts))]
    assert starts[-1] + 0.2 == pytest.approx(3.7)


def test_the_grid_reaches_the_machine_as_lines_of_the_asked_width(tmp_path, make_machine):
    """Г1: the back-parsed program, not the geometry — what the machine will expose.

    A row inside a band of rows carries one 4 mm segment (the horizontal line); a row in
    the gap between bands carries the eight 0.2 mm vertical lines. The fixture's row step
    is 20 um, so a 0.2 mm line is exactly ten rows and a 0.3 mm gap exactly fifteen.
    """
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = grid(machine, width_mm=4.0, height_mm=3.0, line_mm=0.2, gap_mm=0.3)
    job = build_job(pattern.layer, pattern.machine, power=0.5, notes=pattern.notes,
                    spot_compensation=False)
    rows = read_segments(write_gcode(job, tmp_path / "grid.nc"))
    inked = [row for row in rows if row.segments]
    assert len(inked) == 150, "3 mm / 0.02 mm rows, every one of them inked"
    band_rows = [row for row in inked if len(row.segments) == 1]
    gap_rows = [row for row in inked if len(row.segments) == 8]
    assert len(band_rows) == 6 * 10, "six lines along X, ten rows each"
    assert len(gap_rows) == 150 - 60
    assert {round(end - start, 6)
            for row in band_rows for start, end in row.segments} == {4.0}
    assert {round(end - start, 6)
            for row in gap_rows for start, end in row.segments} == {0.2}
    # Placement anchors the run-up at 0, so the ink starts one overscan (5 mm) later.
    assert [start for start, _end in gap_rows[0].segments] == [
        pytest.approx(5.0 + 0.5 * index) for index in range(8)]


def test_grid_announces_bands_the_row_step_cannot_make(make_machine):
    """Г1: a line along X is a band of rows — 0.21 mm on a 0.02 mm step is 10.5 rows."""
    pattern = grid(make_machine(calibration_text=NO_SPOT), line_mm=0.21, gap_mm=0.25)
    text = " | ".join(pattern.warnings)
    assert "0.2 or 0.22" in text, "0.21 mm / 0.02 mm = 10.5 rows"
    assert "0.24 or 0.26" in text, "0.25 mm / 0.02 mm = 12.5 rows"


def test_grid_is_silent_when_both_band_widths_are_whole_rows(make_machine):
    """Г1 control: 0.2 and 0.3 on a 0.02 mm step are 10 and 15 rows — nothing to say."""
    pattern = grid(make_machine(calibration_text=NO_SPOT), line_mm=0.2, gap_mm=0.3)
    assert not any("row step" in warning for warning in pattern.warnings)


def test_grid_says_nothing_about_bands_it_does_not_draw(make_machine):
    """Г1: with lines along Y only, no band is quantised by the row step."""
    pattern = grid(make_machine(calibration_text=NO_SPOT), line_mm=0.21, gap_mm=0.25,
                   direction="y")
    assert not any("row step" in warning for warning in pattern.warnings)


GRID_REFUSAL_CELLS = [
    ({"line_mm": 0.0}, "line", "a line has no width"),
    ({"gap_mm": 0.0}, "gap", "a gap of zero closes the lines into a solid block"),
    ({"direction": "z"}, "direction", "an unknown direction"),
    ({"height_mm": 0.15, "direction": "x"}, "holds no line",
     "too short for one line along X"),
    ({"width_mm": 0.15, "direction": "y"}, "holds no line",
     "too narrow for one line along Y"),
]


@pytest.mark.parametrize("options, message, why", GRID_REFUSAL_CELLS,
                         ids=[cell[2] for cell in GRID_REFUSAL_CELLS])
def test_grid_refuses_requests_it_cannot_draw(make_machine, options, message, why):
    """Г1: a refusal with a word, never a silently different picture."""
    machine = make_machine(calibration_text=NO_SPOT)
    request = {"width_mm": 4.0, "height_mm": 3.0, "line_mm": 0.2, "gap_mm": 0.3, **options}
    with pytest.raises(ValueError, match=message):
        grid(machine, **request)


def test_dose_bands_are_used_by_the_job_as_dose_objects(make_machine):
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = dose(machine, (0.02, 0.05))
    job = build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                    spot_compensation=False)
    assert all(isinstance(level, Dose) for level in job.doses)
    assert job.s_value == 50, "the uniform field carries the highest band S"
    assert isinstance(job.rows[0], ScanRow)


# ── С2 of `plan_2026_10_23_dose_by_speed`: the strip by SPEED ────────────────────────


def test_dose_by_speed_keeps_one_power_and_varies_the_feed(make_machine):
    """C2: tiles at 50 and 25 mm/s, one power. The dose rises upwards, the S does not move.

    Tile boundaries land on row centres: 10 mm / 0.02 = every 500th row.
    """
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = dose(machine, speeds=(50.0, 25.0), power=0.5)
    assert [band.speed_mm_s for band in pattern.bands] == [50.0, 25.0]
    assert [band.power for band in pattern.bands] == [0.5, 0.5]
    job = build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                    notes=pattern.notes, spot_compensation=False)
    assert [level.relative_dose for level in job.doses] == [pytest.approx(0.5),
                                                            pytest.approx(1.0)]
    assert job.row_speeds[499] == 50.0
    assert job.row_speeds[500] == 25.0
    assert set(job.row_s_values) == {500}, "one power word for the whole strip"
    text = "\n".join(render_gcode(job))
    assert ("; dose 1/2: y 0.000-10.000 mm (figure), power 0.5000, S 500, "
            "speed 50 mm/s, relative dose 0.500") in text
    assert ("; dose 2/2: y 10.000-20.000 mm (figure), power 0.5000, S 500, "
            "speed 25 mm/s, relative dose 1.000") in text
    assert "; doses: relative dose = power x 50 mm/s / row speed" in text


SPEED_ORDER_CELLS = [((50.0, 50.0), "equal neighbours"),
                     ((25.0, 50.0), "rising speeds")]


@pytest.mark.parametrize("speeds, why", SPEED_ORDER_CELLS,
                         ids=[cell[1] for cell in SPEED_ORDER_CELLS])
def test_dose_refuses_speeds_that_do_not_fall(make_machine, speeds, why):
    """Dose ∝ 1/v, so a strip rises only if the speed falls; two equal tiles are a waste."""
    machine = make_machine(calibration_text=NO_SPOT)
    with pytest.raises(ValueError, match="must fall"):
        dose(machine, speeds=speeds, power=0.5)


def test_dose_refuses_powers_and_speeds_together(make_machine):
    """One knob per strip: two ladders in one file could not be read back by the operator."""
    machine = make_machine(calibration_text=NO_SPOT)
    with pytest.raises(ValueError, match="not both"):
        dose(machine, (0.2, 0.4), speeds=(50.0, 25.0), power=0.5)


def test_dose_speed_strip_needs_one_power(make_machine):
    """`S` still has to be decided: a speed strip carries ONE power for all its tiles."""
    machine = make_machine(calibration_text=NO_SPOT)
    with pytest.raises(ValueError, match="needs one power"):
        dose(machine, speeds=(50.0, 25.0))


def test_speeds_from_series_falls_geometrically_and_refuses_to_rise():
    """The same ladder as the powers, climbed downwards: 80 … 5 mm/s in equal ratios."""
    levels = speeds_from_series(80.0, 5.0, 9)
    assert levels[0] == pytest.approx(80.0)
    assert levels[-1] == pytest.approx(5.0)
    ratio = (5.0 / 80.0) ** (1.0 / 8.0)
    assert levels[1] == pytest.approx(80.0 * ratio)
    assert all(lower > upper for lower, upper in zip(levels, levels[1:]))
    with pytest.raises(ValueError, match="falls"):
        speeds_from_series(5.0, 80.0, 9)
