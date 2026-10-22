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
