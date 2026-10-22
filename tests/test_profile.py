"""Machine profile and calibration (plan Ш3).

Cells closed here:

    валидный профиль            поля на месте, мкм → мм при выходе
    корабельный профиль         profiles/china_ldi_grbl.toml читается, предупреждения на месте
    нет файла профиля           отказ с путём
    битый TOML                  отказ, а не трассировка
    нет секции                  отказ называет секцию
    нет поля                    отказ называет поле
    вне диапазона               usable_fraction 0 и 1.5, стол 0, прошивка, ось
    шаг мотора                  $100/$101 обязательны, шаг строки — целое число шагов по Y
    шаг строки мимо шагов       отказ с числами, полем и файлом; настоящий профиль — тоже
    калибровки нет              scale 1, skew 0, пятно None, предупреждение строкой
    калибровка есть             значения и дата читаются, мкм → мм
    пятно 0 = не измерено       None и предупреждение
    перекос 11.5°               отказ (ловушка «радианы в градусы»)
    require_spot_radius()       отказ с путём к файлу калибровки, когда пятна нет

Пустые клетки: не проверяется «профиль без [laser]» отдельно — общая клетка
«нет секции» уже держит этот путь; не проверяется отрицательный power_w как
отказ — по design §13.1 мощность модуля может быть неизвестна (0), а «измерить»
пишется комментарием.
"""
from datetime import date
from pathlib import Path

import pytest

from gerber2ldi.errors import ProfileError
from gerber2ldi.job import load_calibration, load_profile

PROFILE = """
[machine]
name = "test-machine"
firmware = "grbl"
steps_per_mm_x = 50
steps_per_mm_y = 50

[table]
size_x_mm = 210
size_y_mm = 270
origin = "front-left"

[laser]
wavelength_nm = 405
power_w = 0.5
usable_fraction = 0.8
s_max = 1000
pwm_hz = 1000

[scan]
scan_axis = "x"
row_step_um = 20
speed_mm_s = 50
overscan_mm = 5
bidirectional = false
"""

CALIBRATION = """
[calibration]
scale_x = 1.002
scale_y = 0.999
skew_deg = 0.05
spot_radius_um = 150
measured = 2026-10-20
"""


def _write(tmp_path, text: str, name: str = "machine.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def _write_calibration(tmp_path, text: str = CALIBRATION, name: str = "machine.calibration.toml"):
    return _write(tmp_path, text, name)


def test_valid_profile_maps_fields_and_units(tmp_path):
    """Micrometres in the file, millimetres out of the module (plan §2.2)."""
    _write_calibration(tmp_path)
    machine = load_profile(_write(tmp_path, PROFILE))
    assert machine.name == "test-machine"
    assert machine.firmware == "grbl"
    assert (machine.table.size_x_mm, machine.table.size_y_mm) == (210.0, 270.0)
    assert machine.scan.row_step_mm == pytest.approx(0.02)
    assert machine.scan.speed_mm_s == 50.0
    assert machine.scan.overscan_mm == 5.0
    assert machine.scan.bidirectional is False
    assert machine.laser.usable_fraction == 0.8
    assert machine.warnings == ()


def test_shipped_profile_loads_and_warns_about_missing_calibration():
    """profiles/china_ldi_grbl.toml is in git and must stay readable."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    machine = load_profile(root / "profiles" / "china_ldi_grbl.toml")
    assert machine.name == "china-ldi"
    assert machine.firmware == "grbl"
    assert (machine.table.size_x_mm, machine.table.size_y_mm) == (210.0, 270.0)
    assert machine.calibration.scale_x == 1.0
    assert machine.calibration.spot_radius_mm is None
    assert len(machine.warnings) == 2
    assert "not calibrated" in machine.warnings[0]
    assert "Spot radius not measured" in machine.warnings[1]


def test_missing_profile_is_a_refusal(tmp_path):
    missing = tmp_path / "nope.toml"
    with pytest.raises(ProfileError, match="not found"):
        load_profile(missing)
    with pytest.raises(ProfileError, match=str(missing)):
        load_profile(missing)


def test_broken_toml_is_a_refusal(tmp_path):
    path = _write(tmp_path, "this is not toml [[[\n")
    with pytest.raises(ProfileError, match="Cannot read profile"):
        load_profile(path)


BAD_PROFILE_CELLS = [
    ("missing section", "[machine]", "[machin]", "section is required"),
    ("missing field", "row_step_um = 20", "", "scan.row_step_um is required"),
    # The motor resolution is mandatory: without it the row step cannot be checked
    # against the microsteps, and a banded exposure looks like a resist problem (Г2).
    ("missing motor steps", "steps_per_mm_x = 50", "", "machine.steps_per_mm_x is required"),
    ("zero motor steps", "steps_per_mm_y = 50", "steps_per_mm_y = 0", "machine.steps_per_mm_y"),
    ("negative motor steps", "steps_per_mm_y = 50", "steps_per_mm_y = -80",
     "machine.steps_per_mm_y"),
    ("usable_fraction zero", "usable_fraction = 0.8", "usable_fraction = 0.0",
     "laser.usable_fraction"),
    ("usable_fraction above one", "usable_fraction = 0.8", "usable_fraction = 1.5",
     "laser.usable_fraction"),
    ("table size zero", "size_x_mm = 210", "size_x_mm = 0", "table.size_x_mm"),
    ("unknown firmware", 'firmware = "grbl"', 'firmware = "marlin"', "machine.firmware"),
    ("unknown axis", 'scan_axis = "x"', 'scan_axis = "z"', "scan.scan_axis"),
    ("unknown origin", 'origin = "front-left"', 'origin = "middle"', "table.origin"),
    ("zero speed", "speed_mm_s = 50", "speed_mm_s = 0", "scan.speed_mm_s"),
    ("negative overscan", "overscan_mm = 5", "overscan_mm = -1", "scan.overscan_mm"),
    ("bidirectional not a bool", "bidirectional = false", 'bidirectional = "yes"',
     "scan.bidirectional"),
    # Not implemented yet: refused with the field named, never quietly ignored —
    # the job writer assumes X scanning in one direction.
    ("Y scanning", 'scan_axis = "x"', 'scan_axis = "y"', "scan.scan_axis"),
    ("two directions", "bidirectional = false", "bidirectional = true",
     "scan.bidirectional"),
]


@pytest.mark.parametrize("name, old, new, expected", BAD_PROFILE_CELLS,
                         ids=[cell[0] for cell in BAD_PROFILE_CELLS])
def test_bad_profile_is_refused_and_names_the_field(tmp_path, name, old, new, expected):
    """Every refusal names the FIELD (and the file), so the operator can fix it."""
    text = PROFILE.replace(old, new)
    assert text != PROFILE or new == ""
    path = _write(tmp_path, text)
    with pytest.raises(ProfileError, match=expected):
        load_profile(path)


def _with_motor(text: str, steps_per_mm_y: str, row_step_um: str) -> str:
    """The profile text with the Y resolution and the row step replaced."""
    return (text.replace("steps_per_mm_y = 50", f"steps_per_mm_y = {steps_per_mm_y}")
            .replace("row_step_um = 20", f"row_step_um = {row_step_um}"))


ROW_STEP_WHOLE_CELLS = [
    (80, "25", 0.025),     # 2 steps of 12.5 µm: the shipped machine
    (80, "12.5", 0.0125),  # exactly one microstep
    (50, "20", 0.02),      # the fixture's machine
]


@pytest.mark.parametrize("steps_per_mm_y, row_step_um, expected_mm", ROW_STEP_WHOLE_CELLS,
                         ids=["80 step/mm, 25 um = 2 steps",
                              "80 step/mm, 12.5 um = 1 step",
                              "50 step/mm, 20 um = 1 step"])
def test_a_row_step_of_whole_motor_steps_is_accepted(tmp_path, steps_per_mm_y,
                                                     row_step_um, expected_mm):
    """Г2: a stepper can only stop on its own microsteps, so a whole count passes."""
    machine = load_profile(_write(
        tmp_path, _with_motor(PROFILE, steps_per_mm_y, row_step_um)))
    assert machine.scan.row_step_mm == pytest.approx(expected_mm)
    assert machine.motor.step_y_mm == pytest.approx(1.0 / steps_per_mm_y)


@pytest.mark.parametrize("steps_per_mm_y, row_step_um", [(80, "20"), (80, "26")],
                         ids=["1.6 steps", "2.08 steps"])
def test_a_row_step_off_the_motor_steps_is_refused(tmp_path, steps_per_mm_y, row_step_um):
    """Г2: 20 µm on a 12.5 µm step puts the rows 12.5 and 25 µm apart in turn.

    The banding was found by hand on 22.10 and looks exactly like a resist problem, so
    the profile refuses it and gives both numbers; the cell checks that the refusal
    names the FIELD and the FILE.
    """
    path = _write(tmp_path, _with_motor(PROFILE, steps_per_mm_y, row_step_um))
    with pytest.raises(ProfileError, match="not a whole number of motor steps"):
        load_profile(path)
    with pytest.raises(ProfileError, match="scan.row_step_um"):
        load_profile(path)
    with pytest.raises(ProfileError, match=str(path)):
        load_profile(path)


def test_the_shipped_profile_row_step_is_whole_motor_steps(tmp_path):
    """Г2 on the REAL machine, not on an invented one: $101 = 80, rows of 25 µm.

    Денис, 22.10: «иначе сторож проверен только на выдуманном станке». The fixture's
    machine is a different, honest one (50 step/mm); this cell holds the one whose
    row step was measured.
    """
    shipped = Path(__file__).resolve().parents[1] / "profiles" / "china_ldi_grbl.toml"
    text = shipped.read_text()
    assert "row_step_um = 25" in text, "this cell is the 2-steps case"
    machine = load_profile(shipped)
    assert machine.motor.steps_per_mm_x == 80
    assert machine.motor.steps_per_mm_y == 80
    assert machine.motor.step_y_mm == pytest.approx(0.0125)
    assert machine.scan.row_step_mm == pytest.approx(0.025)
    # The same profile with the row step that banded the exposure by hand.
    broken = tmp_path / shipped.name
    broken.write_text(text.replace("row_step_um = 25", "row_step_um = 20"))
    with pytest.raises(ProfileError, match="scan.row_step_um"):
        load_profile(broken)
    with pytest.raises(ProfileError, match=str(broken)):
        load_profile(broken)


def test_missing_calibration_is_not_a_refusal(tmp_path):
    """No calibration file: scale 1, skew 0, a warning line — not a refusal."""
    machine = load_profile(_write(tmp_path, PROFILE))
    assert machine.calibration.scale_x == 1.0
    assert machine.calibration.scale_y == 1.0
    assert machine.calibration.skew_deg == 0.0
    assert machine.calibration.spot_radius_mm is None
    assert machine.calibration.measured is None
    assert any("not calibrated" in warning for warning in machine.warnings)


def test_calibration_values_and_date_are_read(tmp_path):
    _write_calibration(tmp_path)
    machine = load_profile(_write(tmp_path, PROFILE))
    calibration = machine.calibration
    assert calibration.scale_x == pytest.approx(1.002)
    assert calibration.scale_y == pytest.approx(0.999)
    assert calibration.skew_deg == pytest.approx(0.05)
    assert calibration.spot_radius_mm == pytest.approx(0.15)  # 150 µm
    assert calibration.measured == date(2026, 10, 20)
    assert machine.warnings == ()


def test_zero_spot_means_not_measured(tmp_path):
    """The template value 0 is "not measured yet", not a zero-radius spot."""
    _write_calibration(tmp_path, CALIBRATION.replace("spot_radius_um = 150",
                                                     "spot_radius_um = 0"))
    machine = load_profile(_write(tmp_path, PROFILE))
    assert machine.calibration.spot_radius_mm is None
    assert any("Spot radius not measured" in warning for warning in machine.warnings)


@pytest.mark.parametrize("old, new, expected", [
    ("skew_deg = 0.05", "skew_deg = 11.5", "calibration.skew_deg"),
    ("scale_x = 1.002", "scale_x = 0", "calibration.scale_x"),
    ("measured = 2026-10-20", 'measured = "20.10.2026"', "calibration.measured"),
    # A negative or non-finite spot used to become "not measured" silently, and the
    # job was then built WITHOUT compensation. TOML itself accepts nan and inf.
    ("spot_radius_um = 150", "spot_radius_um = -150", "calibration.spot_radius_um"),
    ("spot_radius_um = 150", "spot_radius_um = nan", "calibration.spot_radius_um"),
    ("spot_radius_um = 150", "spot_radius_um = inf", "calibration.spot_radius_um"),
    # Same trap as skew: percent instead of a factor, and a 20 % error.
    ("scale_x = 1.002", "scale_x = 100.2", "calibration.scale_x"),
    ("scale_x = 1.002", "scale_x = 1.2", "calibration.scale_x"),
    ("scale_y = 0.999", "scale_y = 0.8", "calibration.scale_y"),
], ids=["skew beyond", "scale zero", "bad date", "spot negative", "spot nan",
        "spot inf", "scale in percent", "scale 1.2", "scale 0.8"])
def test_bad_calibration_is_refused(tmp_path, old, new, expected):
    _write_calibration(tmp_path, CALIBRATION.replace(old, new))
    with pytest.raises(ProfileError, match=expected):
        load_profile(_write(tmp_path, PROFILE))


@pytest.mark.parametrize("scale", ["0.95", "1.05"])
def test_scale_bounds_are_inclusive(tmp_path, scale):
    """The bounds themselves are legal: a real belt stretch lives inside them."""
    text = (CALIBRATION.replace("scale_x = 1.002", f"scale_x = {scale}")
            .replace("scale_y = 0.999", f"scale_y = {scale}"))
    _write_calibration(tmp_path, text)
    machine = load_profile(_write(tmp_path, PROFILE))
    assert machine.calibration.scale_x == pytest.approx(float(scale))
    assert machine.calibration.scale_y == pytest.approx(float(scale))


def test_calibration_may_be_read_alone(tmp_path):
    """A caller that only wants the instance data can read the file on its own."""
    calibration = load_calibration(_write_calibration(tmp_path))
    assert calibration.spot_radius_mm == pytest.approx(0.15)
    assert load_calibration(tmp_path / "absent.toml").scale_x == 1.0


def test_require_spot_radius_refuses_and_names_the_file(tmp_path):
    """Compensation on an invented radius is worse than no compensation."""
    machine = load_profile(_write(tmp_path, PROFILE))
    with pytest.raises(ProfileError, match="Spot radius not measured"):
        machine.require_spot_radius()
    with pytest.raises(ProfileError, match="machine.calibration.toml"):
        machine.require_spot_radius()

    _write_calibration(tmp_path)
    measured = load_profile(_write(tmp_path, PROFILE))
    assert measured.require_spot_radius() == pytest.approx(0.15)
