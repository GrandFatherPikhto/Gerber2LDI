"""The job: calibration chain, hard limits, polarity, side, placement (plan Ш4).

Cells closed here:

    геометрия без компенсации   100 строк по 0.02, рисунок встал на стол с обгоном 5 мм
    компенсация из калибровки   радиус 0.15 сужает медь до (5.15, 14.85) и съедает торцы
    потолок мощности            0.9 при usable_fraction 0.8 — отказ; 0.8 — S800
    S не перешагивает потолок   floor при некруглом s_max
    мощность ноль/минус         отказ, а не «выключено»
    процесс plating-resist      промежуток w сужается до w − 2r (компенсация ПОСЛЕ полярности)
    сторона bottom              зеркало вокруг центра рисунка меняет форму
    перекос на нижней стороне   сдвиг в координатах станка тот же, что у верхней
    постановка на стол          плата с отрицательными координатами встаёт в 0…; --place сдвигает
    вылет за стол на 0.1 мм     отказ с числами
    origin не front-left        отказ «не поддержано»
    пятно не измерено           отказ; с --no-spot-compensation работаем и предупреждаем
    ШИМ крупнее пятна           предупреждение строкой
    пусто после преобразований  отказ, а не пустой файл
"""
import pytest

from gerber2ldi.errors import JobError
from gerber2ldi.job import PROCESS_PLATING, SIDE_BOTTOM, SIDE_TOP, build_job
from gerber2ldi.source import load_layer

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
FOOTER = "M02*\n"

RECT = (HEADER + "%ADD10R,10.000000X2.000000*%\n%LPD*%\nG01*\nD10*\n"
        "X5000000Y1000000D03*\n" + FOOTER)

# Two strips: 0…2 mm and 5…9 mm. Deliberately asymmetric, so a mirror changes
# the shape instead of mapping the figure onto itself.
TWO_STRIPS = (HEADER + "%ADD10R,2.000000X2.000000*%\n%ADD11R,4.000000X2.000000*%\n"
              "%LPD*%\nG01*\nD10*\nX1000000Y1000000D03*\nD11*\n"
              "X7000000Y1000000D03*\n" + FOOTER)

THIN = (HEADER + "%ADD10R,10.000000X0.200000*%\n%LPD*%\nG01*\nD10*\n"
        "X5000000Y100000D03*\n" + FOOTER)

WIDE = (HEADER + "%ADD10R,205.000000X2.000000*%\n%LPD*%\nG01*\nD10*\n"
        "X102500000Y1000000D03*\n" + FOOTER)

# KiCad-style negative coordinates: nothing here may reach the machine as is.
NEGATIVE = (HEADER + "%ADD10R,10.000000X2.000000*%\n%LPD*%\nG01*\nD10*\n"
            "X-5000000Y-50000000D03*\n" + FOOTER)


def _layer(tmp_path, text, name="layer.gbr"):
    path = tmp_path / name
    path.write_text(text)
    return load_layer(path)


def _middle(job):
    inked = [row for row in job.rows if not row.is_empty]
    return inked[len(inked) // 2]


def test_geometry_without_compensation(tmp_path, make_machine):
    """The figure is placed on the table: run-up starts at 0, ink begins at +5."""
    machine = make_machine()
    job = build_job(_layer(tmp_path, RECT), machine, power=0.5,
                    spot_compensation=False)
    assert len(job.rows) == 100  # 2 mm / 0.02
    assert _middle(job).segments == ((5.0, 15.0),)
    assert job.pass_start_x == pytest.approx(0.0)
    assert job.pass_end_x == pytest.approx(20.0)
    assert job.box[1] == pytest.approx(0.0)
    assert job.s_value == 500
    assert job.feed_mm_per_min == pytest.approx(3000.0)


def test_compensation_uses_the_measured_radius(tmp_path, make_machine):
    """150 µm spot: copper narrows by 0.3 mm and the ends erode away.

    Placement anchors the RUN-UP START at `place_mm`, so with compensation the
    copper begins 0.15 mm later and the strip in front of it is that much longer:
    the ink is 9.7 mm wide instead of 10, and it still starts at 5.0.
    """
    job = build_job(_layer(tmp_path, RECT), make_machine(), power=0.5)
    middle = _middle(job)
    assert middle.segments[0][0] == pytest.approx(5.0, abs=1e-9)
    assert middle.segments[0][1] == pytest.approx(14.7, abs=1e-9)
    assert all(row.is_empty for row in job.rows if row.y_mm < 0.0)
    assert sum(1 for row in job.rows if not row.is_empty) == 86


def test_power_above_the_ceiling_refuses(tmp_path, make_machine):
    """The ceiling is a refusal with a message, never a silent clamp."""
    machine = make_machine()
    layer = _layer(tmp_path, RECT)
    with pytest.raises(JobError, match="above the ceiling 0.8"):
        build_job(layer, machine, power=0.9)
    at_ceiling = build_job(layer, machine, power=0.8, spot_compensation=False)
    assert at_ceiling.s_value == 800


def test_s_value_never_crosses_the_ceiling_with_an_odd_s_max(tmp_path, make_machine,
                                                            profile_text):
    """floor, not round: 0.8 · 999 = 799.2 must give 799, never 800."""
    machine = make_machine(profile_text=profile_text.replace("s_max = 1000",
                                                             "s_max = 999"))
    job = build_job(_layer(tmp_path, RECT), machine, power=0.8,
                    spot_compensation=False)
    assert job.s_value == 799
    assert job.s_value <= machine.laser.usable_fraction * machine.laser.s_max


@pytest.mark.parametrize("power", [0.0, -0.1])
def test_zero_or_negative_power_refuses(tmp_path, make_machine, power):
    with pytest.raises(JobError, match="fraction above zero"):
        build_job(_layer(tmp_path, RECT), make_machine(), power=power)


def test_plating_process_exposes_narrowed_gaps(tmp_path, make_machine):
    """plating-resist lights the gaps — and the spot narrows THOSE by 2r.

    Compensation must run after the polarity flip: the other order would widen the
    exposed gap instead of narrowing it. The gap is 3 mm, so with a 0.15 mm radius
    what is lit is 2.7 mm.
    """
    machine = make_machine()
    layer = _layer(tmp_path, TWO_STRIPS)
    etch = build_job(layer, machine, power=0.5, spot_compensation=False)
    plating_plain = build_job(layer, machine, power=0.5, spot_compensation=False,
                              process=PROCESS_PLATING)
    plating_spot = build_job(layer, machine, power=0.5, process=PROCESS_PLATING)

    assert _middle(etch).segments == ((5.0, 7.0), (10.0, 14.0))
    assert _middle(plating_plain).segments == ((5.0, 8.0),)   # gap 2…5, shifted by 3
    narrowed = _middle(plating_spot).segments
    assert len(narrowed) == 1
    assert narrowed[0][1] - narrowed[0][0] == pytest.approx(2.7, abs=1e-9)


def test_bottom_side_mirrors_the_figure(tmp_path, make_machine):
    """Mirror about the ink centre: the asymmetric figure changes shape."""
    machine = make_machine()
    layer = _layer(tmp_path, TWO_STRIPS)
    # The layer itself is the outline here: this cell is about the mirror, not about
    # two different copper layers (see tests/test_frame.py for the frame's own cells).
    top = build_job(layer, machine, power=0.5, spot_compensation=False, side=SIDE_TOP,
                    frame=layer)
    bottom = build_job(layer, machine, power=0.5, spot_compensation=False,
                       side=SIDE_BOTTOM, frame=layer)
    assert _middle(top).segments == ((5.0, 7.0), (10.0, 14.0))
    # Figure-space mirror about 4.5, then the +5 placement shift.
    assert _middle(bottom).segments == ((5.0, 9.0), (12.0, 14.0))


def test_skew_adds_the_same_shift_on_both_sides(tmp_path, make_machine,
                                                calibration_text):
    """Skew AFTER the mirror: the machine-space shift must not flip between sides.

    The other order turns the skew into 2·θ·L between the sides (design §17), so the
    cell compares the shift the skew ADDED at the same height, not an absolute
    position — placement re-anchors the first row, which would hide a global shift.
    """
    skewed = make_machine(calibration_text=calibration_text.replace(
        "skew_deg = 0.0", "skew_deg = 0.1"))
    straight = make_machine()
    layer = _layer(tmp_path, TWO_STRIPS)
    probe_height = 0.5

    def row_near(job, y_mm):
        row = min(job.rows, key=lambda candidate: abs(candidate.y_mm - y_mm))
        assert row.segments, "no ink at the probe height"
        return row

    def shift_of(machine, side):
        with_skew = build_job(layer, machine, power=0.5, side=side, frame=layer)
        plain = build_job(layer, straight, power=0.5, side=side, frame=layer)
        moved = row_near(with_skew, probe_height)
        still = row_near(plain, probe_height)
        assert moved.y_mm == pytest.approx(still.y_mm, abs=1e-9)
        return moved.segments[0][0] - still.segments[0][0]

    top_shift = shift_of(skewed, SIDE_TOP)
    bottom_shift = shift_of(skewed, SIDE_BOTTOM)
    assert top_shift == pytest.approx(bottom_shift, abs=1e-9)
    assert abs(top_shift) > 1e-6, "the skew is supposed to move something"


def test_negative_coordinates_are_placed_on_the_table(tmp_path, make_machine):
    """A KiCad board lives at negative Y; the machine table starts at zero."""
    job = build_job(_layer(tmp_path, NEGATIVE), make_machine(), power=0.5,
                    spot_compensation=False)
    assert job.pass_start_x == pytest.approx(0.0)
    assert job.box[1] == pytest.approx(0.0)
    assert job.box[3] <= 270.0
    # The first pass sits ON the table edge, so its centre is exactly 0 — not below.
    assert all(row.y_mm >= 0.0 for row in job.rows)
    assert min(row.y_mm for row in job.rows) == pytest.approx(0.0)


def test_place_moves_the_pass_and_is_checked_against_the_table(tmp_path, make_machine):
    machine = make_machine()
    layer = _layer(tmp_path, RECT)
    moved = build_job(layer, machine, power=0.5, spot_compensation=False,
                      place_mm=(20.0, 30.0))
    assert moved.pass_start_x == pytest.approx(20.0)
    assert moved.box[1] == pytest.approx(30.0)

    # 20 mm of pass (10 figure + 2 × 5 run-up) at 190 leaves 210 exactly; +0.1 over.
    edge = build_job(layer, machine, power=0.5, spot_compensation=False,
                     place_mm=(190.0, 0.0))
    assert edge.pass_end_x == pytest.approx(210.0)
    with pytest.raises(JobError, match="210.1"):
        build_job(layer, machine, power=0.5, spot_compensation=False,
                  place_mm=(190.1, 0.0))
    with pytest.raises(JobError, match="along"):
        build_job(layer, machine, power=0.5, spot_compensation=False,
                  place_mm=(0.0, 269.0))


def test_unsupported_origin_refuses(tmp_path, make_machine, profile_text):
    machine = make_machine(profile_text=profile_text.replace(
        'origin = "front-left"', 'origin = "back-right"'))
    with pytest.raises(JobError, match="not supported yet"):
        build_job(_layer(tmp_path, RECT), machine, power=0.5,
                  spot_compensation=False)


def test_missing_spot_refuses_unless_switched_off(tmp_path, make_machine):
    """Compensating on an invented radius is worse than not compensating."""
    machine = make_machine(calibration_text="[calibration]\nscale_x = 1.0\n")
    layer = _layer(tmp_path, RECT)
    with pytest.raises(Exception, match="Spot radius not measured"):
        build_job(layer, machine, power=0.5)

    uncalibrated = build_job(layer, machine, power=0.5, spot_compensation=False)
    # The warning no longer names the flag: a calibration pattern switches
    # compensation off without any flag, and the message must not blame the operator
    # for an option they never typed.
    assert any("Spot compensation is off" in warning
               for warning in uncalibrated.warnings)
    assert _middle(uncalibrated).segments == ((5.0, 15.0),)


def test_pwm_coarser_than_the_spot_warns(tmp_path, make_machine, profile_text):
    """speed / pwm_hz above the spot radius means dashes, not a line.

    50 mm/s at 1 kHz is a 0.05 mm period — fine against a 0.15 mm radius; at
    500 mm/s it is 0.5 mm, i.e. three times the spot, and the job says so.
    """
    machine = make_machine(
        profile_text=profile_text.replace("speed_mm_s = 50", "speed_mm_s = 500"))
    job = build_job(_layer(tmp_path, RECT), machine, power=0.5)
    assert any("PWM period" in warning for warning in job.warnings)

    fast_enough = build_job(_layer(tmp_path, RECT, "slow.gbr"), make_machine(), power=0.5)
    assert not any("PWM period" in warning for warning in fast_enough.warnings)


def test_erosion_that_removes_everything_refuses(tmp_path, make_machine):
    """A figure thinner than the spot leaves nothing: refuse, do not write empty."""
    with pytest.raises(JobError, match="The job is empty"):
        build_job(_layer(tmp_path, THIN), make_machine(), power=0.5)


def test_wide_figure_plus_run_up_refuses_with_numbers(tmp_path, make_machine):
    job = None
    with pytest.raises(JobError, match="0.0 … 215.0 mm across"):
        job = build_job(_layer(tmp_path, WIDE), make_machine(), power=0.5,
                        spot_compensation=False)
    assert job is None, "nothing may be built when the pass leaves the table"
