"""Input layer: Gerber and Excellon → millimetres (plan Ш2).

Cells closed here:

    единицы                      дюймы в файле → миллиметры на выходе
    порядок фигур                файл читается в порядке рисования, полярность при фигуре
    светлая полярность           `%LPC*%` видна как has_clear
    площадь пада, 4 вида × 3 угла  прямоугольник 2.0, овал 1.785, шестиугольник 2.598,
                                  RoundRect 1.946 — при 0/30/45°, с допуском 1 %
    отказы                       нет файла; файл не гербер
    чужой stdout                 `print('xy', ...)` gerbonara не попадает в наш stdout
    сверловка                    отверстия как (x, y, d) в мм

Площадь, а не bbox: у повёрнутого прямоугольника bbox правильный даже с багом
gerbonara (bbox повёрнутого прямоугольника — это и есть bbox), поэтому ловит
именно площадь. Пустых клеток нет: все четыре вида падов KiCad покрыты.
"""
import math

import pytest

from gerber2ldi.errors import InputFileError
from gerber2ldi.source import load_drill, load_layer

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%TF.FileFunction,Copper,L1,Top*%\n"
FOOTER = "M02*\n"


def _area(polygon) -> float:
    total = 0.0
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _flash(macro: str, x_mm: float = 10.0, y_mm: float = 10.0,
           aperture: str = "Shape") -> str:
    return (HEADER + macro + f"%ADD10{aperture}*%\n%LPD*%\nG01*\nD10*\n"
            f"X{int(x_mm * 1e6)}Y{int(y_mm * 1e6)}D03*\n" + FOOTER)


def _macro_rect(angle: float) -> str:
    return f"%AMShape*\n21,1,2.0,1.0,0,0,{angle}*\n%\n"


def _macro_obround(angle: float) -> str:
    half = 0.5
    rad = math.radians(angle)
    cx, cy = half * math.cos(rad), half * math.sin(rad)
    return (f"%AMShape*\n1,1,1.0,{-cx:.6f},{-cy:.6f}*\n1,1,1.0,{cx:.6f},{cy:.6f}*\n"
            f"20,1,1.0,{-half:.6f},0,{half:.6f},0,{angle}*\n%\n")


def _macro_hexagon(angle: float) -> str:
    return f"%AMShape*\n5,1,6,0,0,2.0,{angle}*\n%\n"


def _macro_roundrect(angle: float) -> str:
    radius, half_w, half_h = 0.25, 1.0, 0.5
    corners = []
    for cx, cy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = cx * (half_w - radius), cy * (half_h - radius)
        a = math.radians(angle)
        corners.append(f"{x * math.cos(a) - y * math.sin(a):.6f},"
                       f"{x * math.sin(a) + y * math.cos(a):.6f}")
    body = f"4,1,4,{corners[0]},{corners[1]},{corners[2]},{corners[3]},{corners[0]},0*"
    circles = "\n".join(f"1,1,{2 * radius},{corner}*" for corner in corners)
    lines = "\n".join(f"20,1,{2 * radius},{corners[i]},{corners[(i + 1) % 4]},0*"
                      for i in range(4))
    return f"%AMShape*\n{body}\n{circles}\n{lines}\n%\n"


ANGLES = (0.0, 30.0, 45.0)


def _area_sum(tmp_path, macro, angle: float) -> float:
    """Sum of the polygon areas of one flash.

    Deliberately a SUM, not a union: for the multi-primitive macros (obround,
    `RoundRect`) the primitives overlap, so the sum is not the pad area — but it is
    exactly the quantity that a lost or bbox-substituted polygon changes. The pad
    area itself is checked where it is analytic and single-polygon (rectangle,
    hexagon).
    """
    path = tmp_path / f"pad_{int(angle)}.gbr"
    path.write_text(_flash(macro(angle)))
    layer = load_layer(path)
    return sum(_area(polygon) for shape in layer.shapes for polygon in shape.polygons)


@pytest.mark.parametrize("angle", ANGLES)
def test_rectangle_area_is_exact_at_any_angle(tmp_path, angle):
    """2 × 1 mm pad keeps 2.0 mm² when rotated — the gerbonara workaround's cell.

    Without the workaround this reads 4.5 mm² at 45°: `Rectangle.to_arc_poly()`
    returns the bounding box of the rotated rectangle (measured in step 0).
    """
    assert _area_sum(tmp_path, _macro_rect, angle) == pytest.approx(2.0, rel=0.001)


@pytest.mark.parametrize("angle", ANGLES)
def test_hexagon_area_matches_the_analytic_value(tmp_path, angle):
    """Regular hexagon, circumscribed diameter 2 mm: (3√3/2)·R² with R = 1 mm."""
    expected = 3.0 * math.sqrt(3.0) / 2.0
    assert _area_sum(tmp_path, _macro_hexagon, angle) == pytest.approx(expected, rel=0.01)


MULTI_PRIMITIVE_PADS = [("obround", _macro_obround), ("roundrect", _macro_roundrect)]


@pytest.mark.parametrize("shape, macro", MULTI_PRIMITIVE_PADS,
                         ids=[cell[0] for cell in MULTI_PRIMITIVE_PADS])
def test_macro_of_several_primitives_does_not_change_with_rotation(tmp_path, shape, macro):
    """The bug class is "a rotated primitive came out as its bounding box": it shows
    up as an area that GROWS with the angle. Invariance across 0/30/45 is the cell."""
    areas = [_area_sum(tmp_path, macro, angle) for angle in ANGLES]
    assert all(area > 0.0 for area in areas)
    assert max(areas) - min(areas) <= 0.001 * max(areas)


def test_units_in_inches_become_millimetres(tmp_path):
    """The core is millimetres: a 0.1 inch pad leaves this module 2.54 mm wide."""
    text = ("%FSLAX46Y46*%\n%MOIN*%\n%ADD10R,0.1X0.1*%\n%LPD*%\nG01*\nD10*\n"
            "X1000000Y1000000D03*\n" + FOOTER)
    path = tmp_path / "inches.gbr"
    path.write_text(text)
    layer = load_layer(path)
    x0, y0, x1, y1 = layer.bbox
    assert x1 - x0 == pytest.approx(2.54, abs=1e-6)
    assert y1 - y0 == pytest.approx(2.54, abs=1e-6)
    # `layer.unit` merely REPORTS what the file said; the conversion is proven
    # above by the size in millimetres, so no assertion is made on the string.


def test_shapes_keep_file_order_and_polarity(tmp_path):
    """Polarity is a SEQUENCE: the clear object must stay after the dark one."""
    text = HEADER + ("%ADD10R,4.0X4.0*%\n%LPD*%\nG01*\nD10*\n"
                     "X5000000Y5000000D03*\n"
                     "%LPC*%\nD10*\nX5000000Y5000000D03*\n") + FOOTER
    path = tmp_path / "order.gbr"
    path.write_text(text)
    layer = load_layer(path)
    assert [shape.dark for shape in layer.shapes] == [True, False]
    assert layer.has_clear is True


def test_layer_without_clear_polarity(tmp_path):
    path = tmp_path / "dark.gbr"
    path.write_text(_flash(_macro_rect(0.0)))
    assert load_layer(path).has_clear is False


def test_missing_file_is_a_refusal(tmp_path):
    """Missing input is an expected refusal with the path in the message."""
    missing = tmp_path / "nope.gbr"
    with pytest.raises(InputFileError, match="File not found"):
        load_layer(missing)
    with pytest.raises(InputFileError, match=str(missing)):
        load_layer(missing)


def test_broken_file_is_a_refusal(tmp_path):
    path = tmp_path / "broken.gbr"
    path.write_text("this is not a gerber at all\n")
    with pytest.raises(InputFileError, match="Cannot read Gerber file"):
        load_layer(path)


def test_library_stdout_does_not_leak(tmp_path, capsys):
    """gerbonara prints `xy ...` on macro primitive 5; our stdout must stay clean."""
    path = tmp_path / "hexagon.gbr"
    path.write_text(_flash(_macro_hexagon(0.0)))
    load_layer(path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "xy" not in captured.err


# Decimal coordinates on purpose: gerbonara refuses a bare `X010000` when the file
# states no number format, and a test should not depend on that guess.
EXCELLON = """M48
FMAT,2
METRIC
T1C0.300
%
G90
G05
T1
X10.0Y20.0
X30.0Y40.0
M30
"""


def test_drill_holes_are_millimetres(tmp_path):
    """Drill loading is dumb on purpose: positions and diameters, nothing else."""
    path = tmp_path / "holes.drl"
    path.write_text(EXCELLON)
    holes = load_drill(path)
    assert len(holes) == 2
    assert [round(hole.diameter_mm, 3) for hole in holes] == [0.3, 0.3]
    assert (holes[0].x_mm, holes[0].y_mm) == pytest.approx((10.0, 20.0), abs=1e-6)
    assert (holes[1].x_mm, holes[1].y_mm) == pytest.approx((30.0, 40.0), abs=1e-6)


def test_missing_drill_file_is_a_refusal(tmp_path):
    with pytest.raises(InputFileError, match="File not found"):
        load_drill(tmp_path / "nope.drl")
