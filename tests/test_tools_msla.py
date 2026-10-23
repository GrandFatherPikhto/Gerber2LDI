"""`tools/msla_tests.py`: MSLA test masks drawn in exact screen pixels
(plan `plan_2026_10_23_tools_msla_tests`, М1–М2).

The point of the tool is that a mask must not lie about its own numbers: the LCD can
only light whole pixels, so every width is `n = round(w / pitch)` pixels of ITS OWN
axis, and the report says what that came out to in micrometres. The cells here read
the finished PNG back — the picture is the evidence, not the report alone.

Cells:

    PNG                     режим L, ровно W×H пикселей, значения только 0 и 255
    ширина по осям          толщина линии = round(w/pitch) ПО КАЖДОЙ оси, в том числе
                            на неквадратном пикселе (одна ширина — разное n)
    группа                  промежуток = ширине, число белых полос = --lines
    grid край               линия у края: влезает целиком / не влезает на один пиксель
    grid both               объединение `x` и `y`
    зеркало и полярность    --mirror = np.fliplr от прямой, --invert = 255 − прямая
    отказы                  ширина меньше пикселя; рисунок за экран (мм и пиксели);
                            нет --screen-px / --pixel-um
    одинаковые группы       две ширины в одно n — предупреждение; разные n — тишина
    кусок PNG               экран, пиксель, фактические ширины, зеркало, полярность

The script is loaded BY PATH: it is a standalone tool that must not be importable from
the package (no `gerber2ldi` import inside it), so there is nothing to `import` here.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "msla_tests.py"

# A 200 x 120 screen at 34.4 um per pixel: 6.88 x 4.13 mm, enough for the small bars.
SCREEN = ("--screen-px", "200x120")
SQUARE_PIXEL = ("--pixel-um", "34.4x34.4")
# 34.4 um across and 30.0 um down: the same width is 6 px along X and 7 px along Y.
RECT_PIXEL = ("--pixel-um", "34.4x30.0")


def _load_module():
    """Import the script by path.

    The module is registered in `sys.modules` BEFORE it runs: `dataclasses` resolves a
    class' own module by name (Python 3.14), and an unregistered module makes it fail
    with `'NoneType' object has no attribute '__dict__'`.
    """
    spec = importlib.util.spec_from_file_location("msla_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


msla = _load_module()


def _run(tmp_path, *args, name="mask.png") -> Path:
    """Run the tool; a zero exit code is part of the call."""
    target = tmp_path / name
    code = msla.main([*args, "-o", str(target)])
    assert code == 0, f"the tool refused: {code}"
    return target


def _pixels(path: Path) -> np.ndarray:
    return np.array(Image.open(path))


def _runs(values) -> list[tuple[int, int]]:
    """(value, length) runs of one row or column, in order."""
    runs: list[list[int]] = []
    for value in values:
        value = int(value)
        if runs and runs[-1][0] == value:
            runs[-1][1] += 1
        else:
            runs.append([value, 1])
    return [(value, length) for value, length in runs]


def _runs_of_figure(values) -> list[tuple[int, int]]:
    """Runs up to the LAST white pixel: the black margin of the screen is not the mask."""
    values = [int(value) for value in values]
    last = max(index for index, value in enumerate(values) if value)
    return _runs(values[:last + 1])


def test_the_mask_is_the_screen_in_two_values(tmp_path):
    target = _run(tmp_path, "bars", *SCREEN, *SQUARE_PIXEL,
                  "--widths-mm", "0.2,0.1", "--lines", "2")
    image = Image.open(target)
    assert image.mode == "L"
    assert image.size == (200, 120), "the PNG must be exactly the screen"
    assert set(np.unique(_pixels(target)).tolist()) == {0, 255}


def test_a_width_is_whole_pixels_of_its_own_axis(tmp_path):
    """A non-square pixel: 0.2 mm is 6 px across and 7 px down, not one number twice.

    The horizontal block's lines are along X, so their THICKNESS is measured in Y pixels
    (7); the vertical block's lines are along Y, so their WIDTH is in X pixels (6).
    `--at 0,0` puts the figure corner on the PNG corner, so the runs are readable.
    """
    target = _run(tmp_path, "bars", *SCREEN, *RECT_PIXEL, "--widths-mm", "0.2,0.1",
                  "--lines", "2", "--at", "0,0")
    array = _pixels(target)

    column = _runs_of_figure(array[:, 0])
    # 0.2 mm of a 30 um pixel: 6.67 -> 7 px thick, gap = width, two lines per group;
    # then the group gap (one largest width, 7 px) and the 0.1 mm group: 3.33 -> 3 px.
    assert column == [(255, 7), (0, 7), (255, 7), (0, 7),
                      (255, 3), (0, 3), (255, 3)], "Y thickness must follow the Y pixel"

    row = _runs_of_figure(array[0, :])
    assert row[0][0] == 255, "the horizontal block's top line spans its own width"
    assert row[1][0] == 0, "the two blocks are separated"
    # 0.2 mm of a 34.4 um pixel: 5.81 -> 6 px wide, and 0.1 mm -> 3 px.
    assert row[2:] == [(255, 6), (0, 6), (255, 6), (0, 6), (255, 3), (0, 3), (255, 3)]


@pytest.mark.parametrize("lines", [1, 2, 4])
def test_a_group_has_as_many_lines_as_asked_and_the_gap_equals_the_width(tmp_path, lines):
    """One group of `--lines` lines: every white run is the width, every gap too."""
    target = _run(tmp_path, "bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.2",
                  "--lines", str(lines), "--at", "0,0")
    column = _runs_of_figure(_pixels(target)[:, 0])
    assert [length for value, length in column if value == 255] == [6] * lines
    assert [length for value, length in column if value == 0] == [6] * (lines - 1)


GRID_EDGE_CELLS = [
    ("0.5", 2),   # 5 px tall: 2 + 1 + 2 — the second line ends exactly on the edge
    ("0.4", 1),   # 4 px tall: one pixel short of a whole second line
]


@pytest.mark.parametrize("height_mm, expected", GRID_EDGE_CELLS,
                         ids=["second line fits exactly", "one pixel short"])
def test_the_last_line_is_drawn_only_whole(tmp_path, capsys, height_mm, expected):
    """The near edge is flush, the far edge keeps a gap, never a sliver of line.

    100 um pixels: line 0.2 mm = 2 px, gap 0.1 mm = 1 px, period 3 px. A 5 px side
    holds lines at 0 and 3; a 4 px side holds only the first one.
    """
    target = _run(tmp_path, "grid", *SCREEN, "--pixel-um", "100x100",
                  "--width-mm", "1", "--height-mm", height_mm,
                  "--line-mm", "0.2", "--gap-mm", "0.1", "--direction", "x", "--at", "0,0")
    out = capsys.readouterr().out
    assert f"lines: along X {expected}" in out
    rect_y = int(round(float(height_mm) / 0.1))  # 100 um pixels
    column = _pixels(target)[:rect_y, 0]
    assert int((column == 255).sum()) == 2 * expected, "whole lines of 2 px, never a sliver"


def test_both_directions_are_the_union_of_the_two(tmp_path):
    """`both` draws the union — the crossings are lit, nothing is subtracted."""
    masks = {}
    for direction in ("x", "y", "both"):
        masks[direction] = _pixels(_run(
            tmp_path, "grid", *SCREEN, "--pixel-um", "100x100",
            "--width-mm", "1", "--height-mm", "1", "--line-mm", "0.2",
            "--gap-mm", "0.1", "--direction", direction, "--at", "0,0",
            name=f"{direction}.png"))
    assert masks["x"].any() and masks["y"].any()
    assert not np.array_equal(masks["x"], masks["y"])
    assert np.array_equal(masks["both"], np.maximum(masks["x"], masks["y"]))


def test_mirror_and_invert_are_whole_picture_swaps(tmp_path):
    """`--mirror` is a reflection of the finished picture, `--invert` swaps the values."""
    arguments = ("bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.2,0.1", "--lines", "2")
    direct = _pixels(_run(tmp_path, *arguments, name="direct.png"))
    mirrored = _pixels(_run(tmp_path, *arguments, "--mirror", name="mirrored.png"))
    inverted = _pixels(_run(tmp_path, *arguments, "--invert", name="inverted.png"))
    assert np.array_equal(mirrored, np.fliplr(direct))
    assert np.array_equal(inverted, 255 - direct)
    # The control: a picture that is symmetric would pass the two cells above for free.
    assert not np.array_equal(mirrored, direct), "mirroring must actually change the mask"


def test_a_width_below_one_pixel_is_refused(tmp_path, capsys):
    """0.015 mm of a 34.4 um pixel rounds to zero pixels: no line exists, so refuse."""
    target = tmp_path / "bars.png"
    assert msla.main(["bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.015,0.2",
                      "-o", str(target)]) == 2
    captured = capsys.readouterr()
    assert "below one 34.4 um pixel" in captured.err
    assert captured.out == ""
    assert not target.exists(), "a refusal must not leave half a mask behind"


def test_a_figure_off_the_screen_is_refused_in_millimetres_and_pixels(tmp_path, capsys):
    target = tmp_path / "bars.png"
    assert msla.main(["bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.2",
                      "--lines", "2", "--at", "5,0", "-o", str(target)]) == 2
    captured = capsys.readouterr()
    assert "does not fit" in captured.err
    assert "mm" in captured.err and "px" in captured.err, "both units, as the plan asks"
    assert not target.exists()


@pytest.mark.parametrize("missing", ["--screen-px", "--pixel-um"])
def test_the_screen_and_the_pixel_are_mandatory(tmp_path, missing):
    """No invented default: an unverified spec number is worse than a refusal."""
    given = SQUARE_PIXEL if missing == "--screen-px" else SCREEN
    with pytest.raises(SystemExit) as exc:
        msla.main(["grid", *given, "-o", str(tmp_path / "grid.png")])
    assert exc.value.code == 2


def test_widths_that_share_a_pixel_count_are_announced(tmp_path, capsys):
    """Two requested widths on one `n` give identical groups on the plate — say so."""
    _run(tmp_path, "bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.100,0.110",
         "--lines", "2")
    captured = capsys.readouterr()
    assert "give the same 3 px" in captured.err
    assert "identical" in captured.err
    assert "0.100" in captured.err and "0.110" in captured.err


def test_widths_with_different_pixel_counts_say_nothing(tmp_path, capsys):
    """The control: 0.2 mm and 0.1 mm are 6 and 3 px, so there is nothing to warn about."""
    _run(tmp_path, "bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.2,0.1",
         "--lines", "2")
    captured = capsys.readouterr()
    assert "identical" not in captured.err


def test_the_png_remembers_what_it_is(tmp_path):
    """Screen, pixel, ACTUAL widths, mirror and polarity travel inside the file."""
    target = _run(tmp_path, "bars", *SCREEN, *RECT_PIXEL, "--widths-mm", "0.2",
                  "--lines", "2")
    text = Image.open(target).text["msla_tests"]
    assert "screen: 200 x 120 px" in text
    assert "pixel: 34.40 x 30.00 um" in text
    assert "0.200 mm -> 7 px along Y = 210.0 um, 6 px along X = 206.4 um" in text
    assert "mirror: off" in text
    assert "polarity: white = the lines" in text


def test_the_mirrored_file_says_it_is_mirrored(tmp_path):
    """A mask found later must not be silently the wrong way round."""
    target = _run(tmp_path, "bars", *SCREEN, *SQUARE_PIXEL, "--widths-mm", "0.2",
                  "--lines", "2", "--mirror")
    text = Image.open(target).text["msla_tests"]
    assert "mirror: on" in text
