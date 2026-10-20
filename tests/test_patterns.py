"""Calibration patterns as DATA: rectangles, bands, per-row power (plan Ш5).

Cells:

    shapes          прямоугольник → Shape с точным bbox, Layer собирает рамку
    bars            порядок групп, промежуток = ширине, обе ориентации, 60 фигур
    bars без пятна  строится, шапка говорит «spot compensation: off»
    bars ширина     меньше шага строки → предупреждение, а не тишина
    dose            S на строку: три уровня, границы полос на строках
    dose уровень    выше потолка → отказ (не подрезка), полоса без строки → отказ
    dose полосы     пересечение, power вместе с bands, ни одного — отказы
    Job             s_values неверной длины и не-ASCII заметка — отказы

The pattern is a synthetic `Layer` built from `source.Shape` polygons, so it goes
through the SAME `build_job`: the cells below would catch a second path into the job
as soon as its notes or its per-row power drifted from the Gerber one.
"""
import math

import pytest

from gerber2ldi.backends.gcode import render_gcode, write_gcode
from gerber2ldi.errors import JobError
from gerber2ldi.job import Dose, Job, PowerBand, build_job
from gerber2ldi.patterns import bars, dose, powers_from_series
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


def test_dose_bands_are_used_by_the_job_as_dose_objects(make_machine):
    machine = make_machine(calibration_text=NO_SPOT)
    pattern = dose(machine, (0.02, 0.05))
    job = build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                    spot_compensation=False)
    assert all(isinstance(level, Dose) for level in job.doses)
    assert job.s_value == 50, "the uniform field carries the highest band S"
    assert isinstance(job.rows[0], ScanRow)
