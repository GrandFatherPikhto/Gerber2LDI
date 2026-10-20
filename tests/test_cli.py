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
