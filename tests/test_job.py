"""The job: calibration chain, hard limits, polarity, side (plan Ш4).

Cells closed here:

    геометрия без компенсации    100 строк по 0.02, в середине (0, 10), полоса разгона ±5
    компенсация из калибровки    радиус 0.15 сужает медь до (0.15, 9.85) и съедает торцы
    потолок мощности             0.9 при usable_fraction 0.8 — отказ; 0.8 — S800
    мощность ноль/минус          отказ, а не «выключено»
    процесс plating-resist       светим промежутки: (2, 5) между полосами
    сторона bottom               зеркало вокруг центра рисунка меняет форму
    не влезает на стол           рисунок + разгон шире стола — отказ
    пятно не измерено            отказ; с --no-spot-compensation работаем и предупреждаем
    ШИМ крупнее пятна            предупреждение строкой
    пусто после преобразований   отказ, а не пустой файл

Пустые клетки: `scale_x`/`scale_y`/`skew_deg` не проверяются здесь на числах —
их чтение и границы держит test_profile, а их влияние на строки — клетки scan
(масштаб, перекос). Двустороннего сканирования нет: в профиле оно выключено, и
первый шаг его не делает.
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


def _layer(tmp_path, text, name="layer.gbr"):
    path = tmp_path / name
    path.write_text(text)
    return load_layer(path)


def _middle(job):
    inked = [row for row in job.rows if not row.is_empty]
    return inked[len(inked) // 2]


def test_geometry_without_compensation(tmp_path, make_machine):
    machine = make_machine()
    layer = _layer(tmp_path, RECT)
    job = build_job(layer, machine, power=0.5, spot_compensation=False)
    assert len(job.rows) == 100  # 2 mm / 0.02
    assert _middle(job).segments == ((0.0, 10.0),)
    assert job.pass_start_x == pytest.approx(-5.0)
    assert job.pass_end_x == pytest.approx(15.0)
    assert job.s_value == 500


def test_compensation_uses_the_measured_radius(tmp_path, make_machine):
    """150 µm spot: copper narrows by 0.15 on each side and the ends erode away."""
    machine = make_machine()
    layer = _layer(tmp_path, RECT)
    job = build_job(layer, machine, power=0.5)
    middle = _middle(job)
    assert middle.segments[0][0] == pytest.approx(0.15, abs=1e-9)
    assert middle.segments[0][1] == pytest.approx(9.85, abs=1e-9)
    assert all(row.is_empty for row in job.rows if row.y_mm < 0.15)


def test_power_above_the_ceiling_refuses(tmp_path, make_machine):
    """The ceiling is a refusal with a message, never a silent clamp."""
    machine = make_machine()
    layer = _layer(tmp_path, RECT)
    with pytest.raises(JobError, match="above the ceiling 0.8"):
        build_job(layer, machine, power=0.9)
    at_ceiling = build_job(layer, machine, power=0.8, spot_compensation=False)
    assert at_ceiling.s_value == 800


@pytest.mark.parametrize("power", [0.0, -0.1])
def test_zero_or_negative_power_refuses(tmp_path, make_machine, power):
    machine = make_machine()
    layer = _layer(tmp_path, RECT)
    with pytest.raises(JobError, match="fraction above zero"):
        build_job(layer, machine, power=power)


def test_plating_process_exposes_the_gaps(tmp_path, make_machine):
    """plating-resist lights the spaces between copper, inside the figure's extent."""
    machine = make_machine()
    layer = _layer(tmp_path, TWO_STRIPS)
    etch = build_job(layer, machine, power=0.5, spot_compensation=False)
    plating = build_job(layer, machine, power=0.5, spot_compensation=False,
                        process=PROCESS_PLATING)
    assert _middle(etch).segments == ((0.0, 2.0), (5.0, 9.0))
    assert _middle(plating).segments == ((2.0, 5.0),)


def test_bottom_side_mirrors_the_figure(tmp_path, make_machine):
    """Mirror about the ink centre: the asymmetric figure changes shape."""
    machine = make_machine()
    layer = _layer(tmp_path, TWO_STRIPS)
    top = build_job(layer, machine, power=0.5, spot_compensation=False, side=SIDE_TOP)
    bottom = build_job(layer, machine, power=0.5, spot_compensation=False,
                       side=SIDE_BOTTOM)
    assert _middle(top).segments == ((0.0, 2.0), (5.0, 9.0))
    # Centre of the ink is 4.5: (0,2) → (7,9) and (5,9) → (0,4).
    assert _middle(bottom).segments == ((0.0, 4.0), (7.0, 9.0))


def test_figure_plus_run_up_must_fit_the_table(tmp_path, make_machine):
    machine = make_machine()
    layer = _layer(tmp_path, WIDE)  # 205 mm wide, plus 5 mm run-up on each side
    with pytest.raises(JobError, match="215.0 mm across"):
        build_job(layer, machine, power=0.5, spot_compensation=False)


def test_missing_spot_refuses_unless_switched_off(tmp_path, make_machine):
    """Compensating on an invented radius is worse than not compensating."""
    machine = make_machine(calibration_text="[calibration]\nscale_x = 1.0\n")
    layer = _layer(tmp_path, RECT)
    with pytest.raises(Exception, match="Spot radius not measured"):
        build_job(layer, machine, power=0.5)

    uncalibrated = build_job(layer, machine, power=0.5, spot_compensation=False)
    assert any("disabled explicitly" in warning for warning in uncalibrated.warnings)
    assert _middle(uncalibrated).segments == ((0.0, 10.0),)


def test_pwm_coarser_than_the_spot_warns(tmp_path, make_machine, profile_text):
    """speed / pwm_hz above the spot radius means dashes, not a line.

    50 mm/s at 1 kHz is a 0.05 mm period — fine against a 0.15 mm radius; at
    500 mm/s it is 0.5 mm, i.e. three times the spot, and the job says so.
    """
    machine = make_machine(
        profile_text=profile_text.replace("speed_mm_s = 50", "speed_mm_s = 500"))
    layer = _layer(tmp_path, RECT)
    job = build_job(layer, machine, power=0.5)
    assert any("PWM period" in warning for warning in job.warnings)

    fast_enough = build_job(_layer(tmp_path, RECT, "slow.gbr"), make_machine(), power=0.5)
    assert not any("PWM period" in warning for warning in fast_enough.warnings)


def test_erosion_that_removes_everything_refuses(tmp_path, make_machine):
    """A figure thinner than the spot leaves nothing: refuse, do not write an empty job."""
    machine = make_machine()
    layer = _layer(tmp_path, THIN)  # 0.2 mm tall, spot radius 0.15 → gone
    with pytest.raises(JobError, match="The job is empty"):
        build_job(layer, machine, power=0.5)
