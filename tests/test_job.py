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
    скорость полосы (С2)        скорость полосы проходит ОДНО правило профиля: 80 мм/с
                                при полосе 5 мм и 100 мм/с при пределе 83.333 — отказы
                                с числами полосы; 0 мм/с — отказ
    доза полосы (С2)            скорость, относительная доза и E на каждую полосу;
                                E — по канону §18.3 (5 мВт, 50 мм/с, 25 мкм = 400 мДж/см²)
    лазер вкл/выкл (С3)         S всегда s_max и usable_fraction не мешает; --power 0.5 —
                                отказ с советом про --speeds; шапка говорит «on/off»
    ШИМ против шага строки (С3) 50 мм/с при 1000 Гц на строке 20 мкм — предупреждение
                                (без измеренного пятна); 10 мм/с — тишина; настоящий
                                профиль 50 мм/с, 1000 Гц, строка 25 мкм — предупреждение
"""
import pytest

from pathlib import Path

from gerber2ldi.backends.gcode import render_gcode
from gerber2ldi.errors import JobError
from gerber2ldi.job import (PROCESS_PLATING, SIDE_BOTTOM, SIDE_TOP, Job, PowerBand,
                            build_job, energy_mj_cm2, load_profile)
from gerber2ldi.patterns import dose
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

# A 10 µm strip along Y: each row's segment is narrower than one microstep of the
# fixture's machine (20 µm). Narrow in X on purpose — a segment is what snaps.
NARROW = (HEADER + "%ADD10R,0.010000X2.000000*%\n%LPD*%\nG01*\nD10*\n"
          "X5000000Y1000000D03*\n" + FOOTER)

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

    50 mm/s at 5 kHz is a 0.01 mm period — fine against a 0.15 mm radius; at 100 Hz
    the same 50 mm/s gives 0.5 mm, i.e. three times the spot, and the job says so.
    The lever used to be the SPEED (500 mm/s); C1 of `plan_2026_10_23_dose_by_speed`
    now refuses a speed above the machine limit, so the same product v/pwm_hz is
    reached by lowering pwm_hz instead. The property and the asserts are unchanged.
    """
    machine = make_machine(
        profile_text=profile_text.replace("pwm_hz = 5000", "pwm_hz = 100"))
    job = build_job(_layer(tmp_path, RECT), machine, power=0.5)
    assert any("PWM period" in warning for warning in job.warnings)

    fast_enough = build_job(_layer(tmp_path, RECT, "slow.gbr"), make_machine(), power=0.5)
    assert not any("PWM period" in warning for warning in fast_enough.warnings)


@pytest.mark.parametrize("steps_per_mm_x, expected", [("50", True), ("500", False)],
                         ids=["20 um step", "2 um step"])
def test_segments_shorter_than_the_motor_step_are_announced(tmp_path, make_machine,
                                                            profile_text,
                                                            steps_per_mm_x, expected):
    """Г2: a 10 µm edge cannot land on a 20 µm microstep — it snaps to the nearest one.

    Counted, not refused: a board is full of such edges. The 2 µm row is the control —
    the same drawing, only the axis resolution differs, so the warning follows the
    STEP and not the figure.
    """
    machine = make_machine(profile_text=profile_text.replace(
        "steps_per_mm_x = 50", f"steps_per_mm_x = {steps_per_mm_x}"))
    job = build_job(_layer(tmp_path, NARROW), machine, power=0.5,
                    spot_compensation=False)
    said = any("shorter than the X motor step" in warning for warning in job.warnings)
    assert said is expected


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


def test_energy_mj_cm2_is_the_canon_number():
    """C2: `E = P/(v·Δy)` is J/m² and 1 J/m² = 0.1 mJ/cm² — hence the single 1e5.

    The numbers are the canon's own example (design §18.3): 5 mW at 50 mm/s over 25 µm
    rows is 400 mJ/cm². Halving the speed doubles the dose, halving the power halves it,
    and an unmeasured power is `None` — never a zero that reads like a measurement.
    """
    assert energy_mj_cm2(0.005, 1.0, 50.0, 0.025) == pytest.approx(400.0)
    assert energy_mj_cm2(0.005, 1.0, 25.0, 0.025) == pytest.approx(800.0)
    assert energy_mj_cm2(0.005, 0.5, 50.0, 0.025) == pytest.approx(200.0)
    assert energy_mj_cm2(0.0, 1.0, 50.0, 0.025) is None


def _measured_machine(make_machine, profile_text):
    """5 mW measured and 25 µm rows — the canon's numbers, so the dose cells are readable."""
    text = (profile_text.replace("power_w = 0.5", "power_w = 0.005")
            .replace("steps_per_mm_y = 50", "steps_per_mm_y = 80")
            .replace("row_step_um = 20", "row_step_um = 25"))
    return make_machine(profile_text=text)


def test_a_dose_band_carries_its_speed_relative_dose_and_energy(make_machine,
                                                               profile_text):
    """C2: the band's speed is the dose. Tiles at 50 and 25 mm/s, power 0.8.

    Relative dose 0.8 and 1.6 (the slower tile is lit twice as long), and 320 / 640
    mJ/cm² from the canon formula. The ROWS carry the speed in the same shape as `S`.
    """
    machine = _measured_machine(make_machine, profile_text)
    pattern = dose(machine, speeds=(50.0, 25.0), power=0.8)
    job = build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                    spot_compensation=False)
    assert [level.speed_mm_s for level in job.doses] == [50.0, 25.0]
    assert [level.relative_dose for level in job.doses] == [pytest.approx(0.8),
                                                            pytest.approx(1.6)]
    assert [level.energy_mj_cm2 for level in job.doses] == [pytest.approx(320.0),
                                                            pytest.approx(640.0)]
    assert len(job.row_speeds) == len(job.rows)
    assert set(job.row_speeds) == {50.0, 25.0}
    # One power word for the whole strip: only the speed differs between the tiles.
    assert set(job.row_s_values) == {800}


def test_a_band_speed_the_machine_cannot_run_is_refused(make_machine):
    """C1's rule, applied to a BAND, with the band's numbers and before any file exists.

    80 mm/s at 500 mm/s² needs 6.4 mm of run-up; the fixture's strip is 5 mm.
    """
    machine = make_machine()
    pattern = dose(machine, speeds=(80.0, 20.0), power=0.5)
    with pytest.raises(JobError, match="needs 6.4 mm"):
        build_job(pattern.layer, machine, power_bands=pattern.bands,
                  spot_compensation=False)


def test_a_band_speed_above_the_limit_names_the_field(make_machine):
    """The limit branch, not the run-up one: the refusal names the profile field."""
    machine = make_machine()
    pattern = dose(machine, speeds=(100.0, 20.0), power=0.5)
    with pytest.raises(JobError, match="machine.max_rate_x_mm_s"):
        build_job(pattern.layer, machine, power_bands=pattern.bands,
                  spot_compensation=False)


def test_a_band_speed_of_zero_is_refused(tmp_path, make_machine):
    """A band's data is checked like any other: a speed of 0 has no dose to speak of."""
    with pytest.raises(JobError, match="must be above zero"):
        build_job(_layer(tmp_path, RECT), make_machine(),
                  power_bands=(PowerBand(0.0, 2.0, 0.5, 0.0),),
                  spot_compensation=False)


def test_row_speeds_must_match_the_rows(tmp_path, make_machine):
    """Same invariant as the per-row powers: a speed list that missed a row would lie."""
    job = build_job(_layer(tmp_path, RECT), make_machine(), power=0.5,
                    spot_compensation=False)
    with pytest.raises(JobError, match="row speeds"):
        Job(rows=job.rows, box=job.box, overscan_mm=job.overscan_mm,
            speed_mm_s=job.speed_mm_s, s_value=500, power_fraction=0.5,
            side=job.side, process=job.process, v_values=(1.0, 2.0))
    assert job.row_speeds == (50.0,) * len(job.rows)


# ── С3 of `plan_2026_10_23_dose_by_speed`: a laser that only switches ───────────────


def _onoff(make_machine, profile_text, usable_fraction: str | None = None):
    """The fixture machine with an on/off driver instead of a PWM one."""
    text = profile_text.replace('modulation = "pwm"', 'modulation = "onoff"')
    if usable_fraction is not None:
        text = text.replace("usable_fraction = 0.8", f"usable_fraction = {usable_fraction}")
    return make_machine(profile_text=text)


def test_an_onoff_laser_refuses_a_power_fraction(tmp_path, make_machine, profile_text):
    """C3: the driver holds the current, so a fraction has nothing to act on.

    The refusal carries the way out — the speed is the dose there — instead of a clamp.
    """
    machine = _onoff(make_machine, profile_text)
    with pytest.raises(JobError, match="vary --speeds"):
        build_job(_layer(tmp_path, RECT), machine, power=0.5, spot_compensation=False)


def test_an_onoff_laser_writes_s_max_and_ignores_usable_fraction(tmp_path, make_machine,
                                                                 profile_text):
    """`usable_fraction` caps a duty cycle an on/off driver does not have.

    A ceiling of 0.1 would leave a PWM machine at S100; here every row gets S1000, and
    leaving the power out is the normal case rather than a refusal.
    """
    machine = _onoff(make_machine, profile_text, usable_fraction="0.1")
    job = build_job(_layer(tmp_path, RECT), machine, spot_compensation=False)
    assert machine.laser.is_onoff
    assert job.s_value == 1000
    assert set(job.row_s_values) == {1000}
    assert job.power_fraction is None
    at_one = build_job(_layer(tmp_path, RECT), machine, power=1.0,
                       spot_compensation=False)
    assert at_one.s_value == 1000


def test_an_onoff_laser_says_so_in_the_header(tmp_path, make_machine, profile_text):
    """The only way to read why every S is the same number: the header says it."""
    job = build_job(_layer(tmp_path, RECT), _onoff(make_machine, profile_text),
                    spot_compensation=False)
    assert any(note.startswith("laser: on/off, S = 1000") for note in job.notes)


PWM_FLASH_CELLS = [("1000", "50", True), ("1000", "10", False)]


@pytest.mark.parametrize("pwm_hz, speed, expected", PWM_FLASH_CELLS,
                         ids=["50 um flash over 20 um rows", "10 um flash at 10 mm/s"])
def test_the_pwm_flash_is_judged_against_the_row_step(tmp_path, make_machine,
                                                      profile_text, pwm_hz, speed,
                                                      expected):
    """C3: the warning that was SILENT on the 22.10 plate, where no spot was measured.

    A flash every `v / pwm_hz` longer than the row step means the edge of a lit strip is a
    row of dashes: 50 mm/s at 1 kHz is 50 µm against 20 µm rows. The control is the same
    laser at 10 mm/s — 10 µm, shorter than a row, and nothing is said.
    """
    text = (profile_text.replace("pwm_hz = 5000", f"pwm_hz = {pwm_hz}")
            .replace("speed_mm_s = 50", f"speed_mm_s = {speed}"))
    job = build_job(_layer(tmp_path, RECT), make_machine(profile_text=text), power=0.5)
    said = any("PWM draws a flash" in warning for warning in job.warnings)
    assert said is expected


def test_the_pwm_flash_is_announced_on_the_real_profile(tmp_path):
    """C3 on the REAL machine: 50 mm/s, 1000 Hz and 25 µm rows — the 22.10 plate itself."""
    shipped = Path(__file__).resolve().parents[1] / "profiles" / "china_ldi_grbl.toml"
    job = build_job(_layer(tmp_path, RECT), load_profile(shipped), power=0.5,
                    spot_compensation=False)
    assert any("PWM draws a flash every 50.0 um" in warning for warning in job.warnings)


def test_the_diode_profile_writes_one_power_and_two_feeds():
    """C2 + C3 on the real diode profile: tiles at 80 and 20 mm/s, S = 1000 on both.

    This is the strip that goes onto the old table first: the dose is set by the speed,
    the power word never changes, and the 14 mm strip covers the 12.8 mm the top speed
    needs.
    """
    shipped = Path(__file__).resolve().parents[1] / "profiles" / "china_ldi_grbl_sld3232vf.toml"
    machine = load_profile(shipped)
    pattern = dose(machine, speeds=(80.0, 20.0), power=1.0)
    job = build_job(pattern.layer, pattern.machine, power_bands=pattern.bands,
                    spot_compensation=False)
    assert set(job.row_s_values) == {1000}
    assert set(job.row_speeds) == {80.0, 20.0}
    assert job.speed_text == "20-80"
    assert all(level.energy_mj_cm2 is None for level in job.doses)
    assert "absolute dose unknown: power_w not measured" in "\n".join(render_gcode(job))
