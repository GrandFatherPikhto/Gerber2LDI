"""Suite-wide fixtures."""
import pytest

import gerber2ldi.i18n as i18n
from gerber2ldi.job import load_profile


@pytest.fixture(autouse=True)
def _restore_translation(monkeypatch):
    """setup_i18n() (called by the CLI's main() too) swaps the process-wide
    translation; put it back after every test so a language never leaks into
    the next test of the same worker under `-n auto`."""
    monkeypatch.setattr(i18n, "_translation", i18n._translation)


PROFILE_TEXT = """
[machine]
name = "test-machine"
firmware = "grbl"
steps_per_mm_x = 50
steps_per_mm_y = 50
# The ceiling and the acceleration are mandatory (C1 of `plan_2026_10_23_dose_by_speed`).
# 83.333 is the shipped machine's $110/60, so a fixture job is judged by the same limit as
# the real one; accel 500 reaches 50 mm/s in 2.5 mm — half of the 5 mm run-up below, so no
# existing cell has to live on the float tolerance of an exact boundary.
max_rate_x_mm_s = 83.333
accel_x_mm_s2 = 500

[table]
size_x_mm = 210
size_y_mm = 270
origin = "front-left"

[laser]
wavelength_nm = 405
power_w = 0.5
usable_fraction = 0.8
s_max = 1000
# 5000 Hz rather than the shipped 1000: at 50 mm/s that is 10 um per period against the
# 20 um row step, so the PWM warnings stay silent here unless a cell moves this number
# on purpose (test_pwm_coarser_than_the_spot_warns sets 100).
pwm_hz = 5000
# The fixture machine pulses the beam, like the shipped module: S is the dose and
# usable_fraction is a real ceiling. An on/off machine is a cell's own copy.
modulation = "pwm"

[scan]
scan_axis = "x"
row_step_um = 20
speed_mm_s = 50
overscan_mm = 5
bidirectional = false
"""

CALIBRATION_TEXT = """
[calibration]
scale_x = 1.0
scale_y = 1.0
skew_deg = 0.0
spot_radius_um = 150
measured = 2026-10-20
"""


@pytest.fixture
def profile_text() -> str:
    """The profile as TEXT: a test that needs one value changed edits the text."""
    return PROFILE_TEXT


@pytest.fixture
def calibration_text() -> str:
    return CALIBRATION_TEXT


@pytest.fixture
def make_machine(tmp_path):
    """Build a machine from text: profiles are data, so tests write them as data."""
    counter = {"n": 0}

    def factory(profile_text: str = PROFILE_TEXT,
                calibration_text: str | None = CALIBRATION_TEXT):
        counter["n"] += 1
        stem = f"machine{counter['n']}"
        profile = tmp_path / f"{stem}.toml"
        profile.write_text(profile_text)
        if calibration_text is not None:
            (tmp_path / f"{stem}.calibration.toml").write_text(calibration_text)
        return load_profile(profile)

    return factory
