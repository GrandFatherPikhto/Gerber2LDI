"""The CLI: starts, prints help and version, and keeps its output clean."""
import pytest

from gerber2ldi.cli import main

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
FOOTER = "M02*\n"

# A macro with primitive 5 is deliberate: gerbonara prints `xy <x> <y>` to stdout
# from aperture_macros/primitive.py:230 for exactly this primitive, so this file
# proves the input layer keeps the library's stray output out of our stdout.
POLYGON_GERBER = (HEADER + "%AMHex*\n5,1,6,0,0,2.0,0*\n%\n%ADD10Hex*%\n%LPD*%\n"
                  "G01*\nD10*\nX5000000Y5000000D03*\n" + FOOTER)

RECT_GERBER = (HEADER + "%ADD10R,10.0X2.0*%\n%LPD*%\nG01*\nD10*\n"
               "X5000000Y1000000D03*\n" + FOOTER)

# Board outline around the strip: only its bounding box is used.
FRAME_GERBER = (HEADER + "%ADD10R,40.0X30.0*%\n%LPD*%\nG01*\nD10*\n"
                "X20000000Y15000000D03*\n" + FOOTER)


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """Pin the language for this module.

    The assertions below quote our own user-facing strings, and the CLI detects the
    language from the environment — on a Russian machine the output is translated,
    so without this fixture the test would measure the machine's locale instead of
    the CLI. `LANGUAGE` wins over `LC_ALL`/`LANG` (see `i18n.detect_language`).
    """
    monkeypatch.setenv("LANGUAGE", "en")


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: gerber2ldi" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("gerber2ldi ")


def test_info_reports_what_is_in_the_layer(tmp_path, capsys):
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    assert main(["info", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Size: 10.000 x 2.000 mm" in out
    assert "Apertures: 1" in out
    assert "Clear polarity present: no" in out


def test_info_prints_only_its_own_lines(tmp_path, capsys):
    """The step-0 finding: gerbonara's `print('xy', ...)` must not reach stdout."""
    path = tmp_path / "hex.gbr"
    path.write_text(POLYGON_GERBER)
    assert main(["info", str(path)]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 6
    assert not any(line.startswith("xy") for line in lines)
    assert "xy" not in captured.err


def test_missing_file_is_one_line_and_non_zero(tmp_path, capsys):
    """An expected refusal: one line on stderr, nothing on stdout, no traceback."""
    code = main(["info", str(tmp_path / "nope.gbr")])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "File not found" in captured.err


def test_raster_writes_the_preview(tmp_path, capsys):
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    target = tmp_path / "preview.tif"
    code = main(["raster", str(path), "-o", str(target),
                 "--step-x-mm", "0.1", "--step-y-mm", "0.1"])
    assert code == 0
    assert target.is_file()
    assert "Wrote" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--step-x-mm", "--step-y-mm"])
def test_raster_refuses_a_non_positive_step(tmp_path, flag):
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    with pytest.raises(SystemExit) as exc:
        main(["raster", str(path), "-o", str(tmp_path / "p.tif"), flag, "0"])
    assert exc.value.code == 2


def _machine_files(tmp_path, profile_text, calibration_text):
    profile = tmp_path / "machine.toml"
    profile.write_text(profile_text)
    (tmp_path / "machine.calibration.toml").write_text(calibration_text)
    return profile


def test_job_writes_the_program(tmp_path, capsys, profile_text, calibration_text):
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    target = tmp_path / "layer.nc"
    code = main(["job", str(path), "--profile", str(profile),
                 "--power", "0.5", "-o", str(target)])
    assert code == 0
    assert target.is_file()
    out = capsys.readouterr().out
    assert "Wrote" in out and "S500" in out


def test_job_places_both_sides_on_the_board_outline(tmp_path, capsys, profile_text,
                                                    calibration_text):
    """`--frame` is the rectangle both sides are mirrored and placed by (plan Ш7)."""
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    frame = tmp_path / "Edge_Cuts.gbr"
    frame.write_text(FRAME_GERBER)
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    target = tmp_path / "layer.nc"
    assert main(["job", str(path), "--profile", str(profile), "--power", "0.5",
                 "--frame", str(frame), "-o", str(target)]) == 0
    assert "frame: x " in target.read_text()
    capsys.readouterr()
    # Without the outline a WARNING, not a refusal: one side is a legitimate run.
    assert main(["job", str(path), "--profile", str(profile), "--power", "0.5",
                 "--side", "bottom", "-o", str(target)]) == 0
    captured = capsys.readouterr()
    assert "--frame" in captured.err


def test_job_refuses_power_above_the_ceiling(tmp_path, capsys, profile_text,
                                             calibration_text):
    """A refusal is one line on stderr, exit 2, and no half-written program."""
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    target = tmp_path / "layer.nc"
    assert main(["job", str(path), "--profile", str(profile),
                 "--power", "0.9", "-o", str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "above the ceiling" in captured.err
    assert not target.exists()


def test_preview_draws_the_program_back(tmp_path, capsys, profile_text,
                                        calibration_text):
    path = tmp_path / "rect.gbr"
    path.write_text(RECT_GERBER)
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    program = tmp_path / "layer.nc"
    assert main(["job", str(path), "--profile", str(profile), "--power", "0.5",
                 "-o", str(program)]) == 0
    picture = tmp_path / "preview.png"
    assert main(["preview", str(program), "-o", str(picture)]) == 0
    assert picture.is_file()
    assert "Wrote" in capsys.readouterr().out


NO_SPOT = "[calibration]\nscale_x = 1.0\nscale_y = 1.0\nskew_deg = 0.0\n"


def test_pattern_bars_needs_no_measured_spot(tmp_path, capsys, profile_text):
    """The bars MEASURE the spot, so the command must not demand the measurement."""
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "bars.nc"
    assert main(["pattern", "bars", "--profile", str(profile), "--power", "0.6",
                 "-o", str(target)]) == 0
    out = capsys.readouterr().out
    assert "0.200" in out and "Wrote" in out
    assert "; spot compensation: off" in target.read_text()


PATTERN_COMMANDS = [
    ("bars", ["--power", "0.6"]),
    ("dose", ["--powers", "0.02,0.05"]),
    ("square", ["--power", "0.6"]),
]


@pytest.mark.parametrize("kind, options", PATTERN_COMMANDS,
                         ids=[command[0] for command in PATTERN_COMMANDS])
def test_a_pattern_does_not_talk_about_the_board_frame(tmp_path, capsys, profile_text,
                                                       kind, options):
    """A calibration pattern is not a board, so the `--frame` hint means nothing there.

    Г3 of `plan_2026_10_22_pattern_grid`: the warning came from `build_job`, which cannot
    tell a board from a pattern; `board=False` is how a pattern says which one it is. The
    cell checks BOTH streams: the hint is a warning, and warnings go to stderr.
    """
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / f"{kind}.nc"
    assert main(["pattern", kind, "--profile", str(profile), *options,
                 "-o", str(target)]) == 0
    captured = capsys.readouterr()
    assert "--frame" not in captured.out
    assert "--frame" not in captured.err


def test_pattern_dose_prints_the_bottom_to_top_mapping(tmp_path, capsys,
                                                       profile_text):
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "dose.nc"
    assert main(["pattern", "dose", "--profile", str(profile),
                 "--powers", "0.02,0.05", "-o", str(target)]) == 0
    out = capsys.readouterr().out
    assert "bottom to top" in out
    assert "power 0.02" in out and "S 20" in out
    assert "power 0.05" in out and "S 50" in out
    assert target.read_text().count("M4 S0") == target.read_text().count("M5") - 1


def test_pattern_dose_needs_exactly_one_form_of_levels(tmp_path, capsys,
                                                       profile_text):
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "dose.nc"
    assert main(["pattern", "dose", "--profile", str(profile),
                 "-o", str(target)]) == 2
    assert "needs its levels" in capsys.readouterr().err
    assert main(["pattern", "dose", "--profile", str(profile), "--powers", "0.02",
                 "--from", "0.02", "--to", "0.1", "--count", "3",
                 "-o", str(target)]) == 2
    assert "not both" in capsys.readouterr().err
    assert not target.exists()


def test_pattern_square_states_whether_the_calibration_was_applied(tmp_path, capsys,
                                                                   profile_text,
                                                                   calibration_text):
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    target = tmp_path / "square.nc"
    assert main(["pattern", "square", "--profile", str(profile), "--power", "0.6",
                 "-o", str(target)]) == 0
    assert "not applied" in capsys.readouterr().out
    assert main(["pattern", "square", "--profile", str(profile), "--power", "0.6",
                 "--with-calibration", "-o", str(target)]) == 0
    out = capsys.readouterr().out
    # The formula says MULTIPLY, because a scale is a factor, not an offset.
    assert "scale_x_new = 1.000000 * (measured X side / 150.000 mm)" in out
    assert "scale_x_new = 1.000000 * (measured X side / 150.000 mm)" in target.read_text()
    assert "calibration: applied" in target.read_text()


def test_pattern_square_too_big_for_the_table_is_one_line(tmp_path, capsys,
                                                          profile_text,
                                                          calibration_text):
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    target = tmp_path / "square.nc"
    assert main(["pattern", "square", "--profile", str(profile), "--power", "0.6",
                 "--side-mm", "205", "-o", str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "across" in captured.err
    assert not target.exists()


def test_pattern_grid_fills_the_rectangle_and_prints_the_counts(tmp_path, capsys,
                                                                profile_text):
    """Г1: the operator reads the numbers off the terminal AND off the header.

    A grid has nothing to label the board with, so the printed counts are the label: how
    many lines along each axis, the period, and whether the spot compensation was on.
    """
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "grid.nc"
    assert main(["pattern", "grid", "--profile", str(profile), "--power", "0.6",
                 "--width-mm", "4", "--height-mm", "3", "--line-mm", "0.2",
                 "--gap-mm", "0.3", "-o", str(target)]) == 0
    out = capsys.readouterr().out
    assert "Grid 4.0 x 3.0 mm" in out
    assert "Lines along X: 6, along Y: 8, direction both" in out
    assert "Spot compensation: off" in out
    assert "; pattern: grid, 4.000 x 3.000 mm" in target.read_text()


def test_pattern_grid_too_short_for_a_line_is_one_line(tmp_path, capsys, profile_text):
    """A pattern refusal is one line, no traceback, and no file left behind."""
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "grid.nc"
    assert main(["pattern", "grid", "--profile", str(profile), "--power", "0.6",
                 "--height-mm", "0.1", "-o", str(target)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "holds no line" in captured.err
    assert not target.exists()


def test_pattern_grid_says_whether_the_spot_compensation_was_used(tmp_path, capsys,
                                                                  profile_text,
                                                                  calibration_text):
    """Г1: off by default, on with the measured radius — said in stdout and the header."""
    profile = _machine_files(tmp_path, profile_text, calibration_text)
    target = tmp_path / "grid.nc"
    options = ["pattern", "grid", "--profile", str(profile), "--power", "0.6",
               "--width-mm", "20", "--height-mm", "10", "--line-mm", "2",
               "--gap-mm", "1", "-o", str(target)]
    assert main(options) == 0
    assert "Spot compensation: off" in capsys.readouterr().out
    assert main([*options[:-2], "--with-compensation", *options[-2:]]) == 0
    assert "Spot compensation: on" in capsys.readouterr().out
    assert "; spot compensation: r=0.150 mm" in target.read_text()


# ── С2 of `plan_2026_10_23_dose_by_speed`: the strip by speed ───────────────────────


def test_pattern_dose_by_speed_reports_speed_and_relative_dose(tmp_path, capsys,
                                                               profile_text):
    """C2: every tile line carries its speed and how much light it gets, in stdout and
    in the header. The fixture's power_w is measured, so the absolute dose is a number."""
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "dose.nc"
    assert main(["pattern", "dose", "--profile", str(profile), "--speeds", "50,25",
                 "--power", "0.5", "-o", str(target)]) == 0
    out = capsys.readouterr().out
    assert "speed 50 mm/s, relative dose 0.500" in out
    assert "speed 25 mm/s, relative dose 1.000" in out
    assert "mJ/cm2" in out, "power_w is measured in the fixture, so E is known"
    text = target.read_text()
    assert "speed 25 mm/s, relative dose 1.000" in text
    assert "absolute dose unknown" not in text


def test_pattern_dose_by_speed_takes_a_geometric_series(tmp_path, capsys, profile_text):
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "dose.nc"
    assert main(["pattern", "dose", "--profile", str(profile), "--speed-from", "50",
                 "--speed-to", "25", "--count", "2", "--power", "0.5",
                 "-o", str(target)]) == 0
    out = capsys.readouterr().out
    assert "speed 50 mm/s" in out and "speed 25 mm/s" in out


def test_pattern_dose_by_speed_refuses_a_tile_it_cannot_run(tmp_path, capsys,
                                                            profile_text):
    """80 mm/s needs 6.4 mm of run-up on this accel; the strip is 5 mm — no file is left."""
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "dose.nc"
    assert main(["pattern", "dose", "--profile", str(profile), "--speeds", "80,20",
                 "--power", "0.5", "-o", str(target)]) == 2
    captured = capsys.readouterr()
    assert "needs 6.4 mm" in captured.err
    assert not target.exists()


FLAG_CLASH_CELLS = [
    (["--speeds", "50,25", "--powers", "0.2,0.4"], "not both", "powers and speeds"),
    (["--speeds", "50,25"], "one power", "a speed strip without a power"),
    (["--speeds", "50,50", "--power", "0.5"], "must fall", "equal neighbours"),
    (["--speed-from", "50", "--speed-to", "25", "--power", "0.5"], "go together",
     "half a speed series"),
]


@pytest.mark.parametrize("options, expected, why", FLAG_CLASH_CELLS,
                         ids=[cell[2] for cell in FLAG_CLASH_CELLS])
def test_pattern_dose_refuses_clashing_level_flags(tmp_path, capsys, profile_text,
                                                   options, expected, why):
    """One refusal line, exit 2, and nothing written — the same contract as everywhere."""
    profile = _machine_files(tmp_path, profile_text, NO_SPOT)
    target = tmp_path / "dose.nc"
    assert main(["pattern", "dose", "--profile", str(profile), *options,
                 "-o", str(target)]) == 2
    assert expected in capsys.readouterr().err
    assert not target.exists()
