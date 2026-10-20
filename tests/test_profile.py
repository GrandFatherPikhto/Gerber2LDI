"""Machine profile and calibration (plan Ш3).

Cells closed here:

    валидный профиль            поля на месте, мкм → мм при выходе
    корабельный профиль         profiles/china_ldi_grbl.toml читается, предупреждения на месте
    нет файла профиля           отказ с путём
    битый TOML                  отказ, а не трассировка
    нет секции                  отказ называет секцию
    нет поля                    отказ называет поле
    вне диапазона               usable_fraction 0 и 1.5, стол 0, прошивка, ось
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

import pytest

from gerber2ldi.errors import ProfileError
from gerber2ldi.job import load_calibration, load_profile

PROFILE = """
[machine]
name = "test-machine"
firmware = "grbl"

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
], ids=["skew beyond", "scale zero", "bad date"])
def test_bad_calibration_is_refused(tmp_path, old, new, expected):
    _write_calibration(tmp_path, CALIBRATION.replace(old, new))
    with pytest.raises(ProfileError, match=expected):
        load_profile(_write(tmp_path, PROFILE))


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
