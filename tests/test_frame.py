"""The board outline is what both sides are mirrored and placed by (plan Ш7).

The defect these cells exist for: `build_job` mirrored the bottom side about the
centre of ITS OWN copper and placed it by ITS OWN copper. F_Cu and B_Cu have
different copper, so the two sides of one board landed at different places on the
table — silently: the file was built and the preview looked fine.

The cell builds a synthetic board whose copper is deliberately lopsided and whose
VIA sits at ONE figure point on both layers. After mirroring the bottom and flipping
the board left-to-right about the frame axis, that via must land on the top layer's
via within 0.001 mm. Mutation "frame = own copper" reddens it.
"""
import math

import pytest

from gerber2ldi.errors import JobError
from gerber2ldi.job import SIDE_BOTTOM, SIDE_TOP, build_job
from gerber2ldi.scan import ScanRow
from gerber2ldi.source import load_layer

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n"
FOOTER = "M02*\n"

# Board 100 x 80 with the origin at a corner; the outline is one flashed rectangle:
# only its bounding box is used, which is all a frame has to give here.
FRAME = ("%ADD10R,100.000000X80.000000*%\n%LPD*%\nG01*\nD10*\n"
         "X50000000Y40000000D03*\n")

# F_Cu: a wide pad at (25, 25) and the via at (40, 10).
FRONT = ("%ADD11R,20.000000X10.000000*%\n%ADD12R,0.500000X0.500000*%\n%LPD*%\nG01*\n"
         "D11*\nX25000000Y25000000D03*\n"
         "D12*\nX40000000Y10000000D03*\n")

# B_Cu: a pad elsewhere (70, 60) and the SAME via at (40, 10).
BACK = ("%ADD21R,30.000000X5.000000*%\n%ADD22R,0.500000X0.500000*%\n%LPD*%\nG01*\n"
        "D21*\nX70000000Y60000000D03*\n"
        "D22*\nX40000000Y10000000D03*\n")

NO_CALIBRATION = "[calibration]\nscale_x = 1.0\nscale_y = 1.0\nskew_deg = 0.0\n"
VIA_FIGURE_MM = (40.0, 10.0)
FRAME_MM = 100.0


def _layer(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(HEADER + body + FOOTER)
    return load_layer(path)


def _calibration(scale_x: float, scale_y: float, skew: float) -> str:
    return ("[calibration]\n"
            f"scale_x = {scale_x}\nscale_y = {scale_y}\nskew_deg = {skew}\n")


def _both_sides(tmp_path, machine, *, place=(10.0, 10.0)):
    frame = _layer(tmp_path, "Edge_Cuts.gbr", FRAME)
    top = _layer(tmp_path, "F_Cu.gbr", FRONT)
    bottom = _layer(tmp_path, "B_Cu.gbr", BACK)
    common = dict(power=0.5, spot_compensation=False, place_mm=place, frame=frame)
    return (build_job(top, machine, side=SIDE_TOP, **common),
            build_job(bottom, machine, side=SIDE_BOTTOM, **common),
            frame)


def _smallest_segment_centre(rows, y_near: float, *, tolerance: float = 0.3) -> float:
    """The centre of the narrowest segment near a row height — the via, by size."""
    best: tuple[float, float] | None = None
    for row in rows:
        if abs(row.y_mm - y_near) > tolerance:
            continue
        for start, end in row.segments:
            length = end - start
            if best is None or length < best[0]:
                best = (length, (start + end) / 2.0)
    assert best is not None, f"no segment near y = {y_near}"
    return best[1]


def _to_real(x_cmd: float, y_cmd: float, calibration) -> tuple[float, float]:
    """The model of the machine: what it really draws at a commanded point.

    Real coordinates are the PHYSICAL table frame (X perpendicular to Y), so the drawn
    board is a rectangle there. Flipping the blank left-to-right is a vertical mirror
    in THIS frame; in commanded coordinates a skewed board is a parallelogram and a
    vertical mirror would not be the same motion.
    """
    y_real = y_cmd * calibration.scale_y
    x_real = (x_cmd * calibration.scale_x
              + y_real * math.tan(math.radians(calibration.skew_deg)))
    return x_real, y_real


def _real_rows(job, calibration) -> tuple[ScanRow, ...]:
    drawn = []
    for row in job.rows:
        if row.is_empty:
            continue
        starts = tuple(_to_real(start, row.y_mm, calibration)[0]
                       for start, _end in row.segments)
        ends = tuple(_to_real(end, row.y_mm, calibration)[0]
                     for _start, end in row.segments)
        drawn.append(ScanRow(_to_real(0.0, row.y_mm, calibration)[1],
                             tuple(zip(starts, ends))))
    return tuple(drawn)


def _real_frame_box(job, calibration) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = job.frame_box
    corners = [_to_real(x, y, calibration)
               for x in (x0, x1) for y in (y0, y1)]
    return (min(p[0] for p in corners), min(p[1] for p in corners),
            max(p[0] for p in corners), max(p[1] for p in corners))


def test_both_sides_are_placed_on_the_same_frame(tmp_path, make_machine):
    machine = make_machine(calibration_text=NO_CALIBRATION)
    top, bottom, _frame = _both_sides(tmp_path, machine)
    assert top.frame_box == bottom.frame_box
    assert top.frame_box[0] - top.overscan_mm == pytest.approx(10.0)
    assert top.frame_box[1] == pytest.approx(10.0)
    assert top.frame_box[2] - top.frame_box[0] == pytest.approx(FRAME_MM)
    # The copper differs, and that is the whole point: the sides must line up anyway.
    assert top.box != bottom.box


CALIBRATIONS = [
    ("neutral", 1.0, 1.0, 0.0),
    ("scale x", 1.002, 1.0, 0.0),
    ("scale y", 1.0, 0.998, 0.0),
    ("skew", 1.0, 1.0, 0.1),
    ("all three", 1.002, 0.998, 0.1),
]


@pytest.mark.parametrize("name, scale_x, scale_y, skew", CALIBRATIONS,
                         ids=[case[0] for case in CALIBRATIONS])
def test_the_via_lands_on_itself_after_the_board_is_flipped(
        tmp_path, make_machine, name, scale_x, scale_y, skew):
    """Flip left-to-right about the frame axis: the two vias coincide within 0.001.

    Both sides are placed on the same FRAME, so in real coordinates the board occupies
    the same rectangle on both runs; flipping the blank about its centre brings the
    bottom via onto the top one. A flip about a skewed copy's bounding box would not:
    that is why the rows are mapped to real coordinates first.
    """
    machine = make_machine(calibration_text=_calibration(scale_x, scale_y, skew))
    top, bottom, _frame = _both_sides(tmp_path, machine)
    assert top.frame_box == bottom.frame_box
    calibration = machine.calibration
    top_real = _real_rows(top, calibration)
    bottom_real = _real_rows(bottom, calibration)
    frame_real = _real_frame_box(top, calibration)
    y_via = frame_real[1] + VIA_FIGURE_MM[1]
    x_top = _smallest_segment_centre(top_real, y_via)
    x_bottom = _smallest_segment_centre(bottom_real, y_via)
    centre = (frame_real[0] + frame_real[2]) / 2.0
    assert 2.0 * centre - x_bottom == pytest.approx(x_top, abs=0.001)


@pytest.mark.parametrize("scale_x, scale_y, skew", [(1.002, 0.998, 0.1), (1.0, 1.0, 0.0)],
                         ids=["calibrated", "neutral"])
def test_the_frame_keeps_its_physical_size_through_the_calibration(
        tmp_path, make_machine, scale_x, scale_y, skew):
    """The drawn board is exactly the outline: 100 x 80 mm on the real table.

    This is what `_frame_box` is for — the frame goes through the same scale and skew
    as the rows. The commanded box is a parallelogram once the skew is in, so its width
    is measured on one row: the bounding box would be wider by ``dy · tg θ``.
    """
    machine = make_machine(calibration_text=_calibration(scale_x, scale_y, skew))
    top, _bottom, _frame = _both_sides(tmp_path, machine)
    x0, y0, x1, y1 = top.frame_box
    tan_skew = math.tan(math.radians(skew))
    width_on_one_row = (x1 - x0) - (y1 - y0) * tan_skew
    assert width_on_one_row * scale_x == pytest.approx(FRAME_MM, abs=0.01)
    assert (y1 - y0) * scale_y == pytest.approx(80.0, abs=0.01)


def test_the_bottom_side_without_a_frame_is_refused(tmp_path, make_machine):
    """Its own copper would shift it against the top side — a refusal, not a guess."""
    machine = make_machine(calibration_text=NO_CALIBRATION)
    bottom = _layer(tmp_path, "B_Cu.gbr", BACK)
    with pytest.raises(JobError, match="common frame"):
        build_job(bottom, machine, side=SIDE_BOTTOM, power=0.5,
                  spot_compensation=False)


def test_the_top_side_without_a_frame_still_works_and_says_so(tmp_path, make_machine):
    machine = make_machine(calibration_text=NO_CALIBRATION)
    top_layer = _layer(tmp_path, "F_Cu.gbr", FRONT)
    job = build_job(top_layer, machine, power=0.5, spot_compensation=False)
    assert job.frame_box is None
    assert any("No common frame" in warning for warning in job.warnings)


def test_copper_that_passes_the_outline_is_announced(tmp_path, make_machine):
    """The frame places the board; copper outside it is a design error, not a shift."""
    machine = make_machine(calibration_text=NO_CALIBRATION)
    frame = _layer(tmp_path, "Edge_Cuts.gbr", FRAME)
    outside = ("%ADD31R,20.000000X10.000000*%\n%LPD*%\nG01*\nD31*\n"
               "X98000000Y25000000D03*\n")  # 88 … 108 crosses the outline at 100
    layer = _layer(tmp_path, "F_Cu.gbr", outside)
    job = build_job(layer, machine, power=0.5, spot_compensation=False, frame=frame)
    assert any("sticks out of the board frame" in warning for warning in job.warnings)


def test_the_table_check_uses_the_frame(tmp_path, make_machine):
    """A frame too wide for the table is refused even when the copper is small."""
    machine = make_machine(calibration_text=NO_CALIBRATION)
    wide = ("%ADD10R,200.000000X80.000000*%\n%LPD*%\nG01*\nD10*\n"
            "X100000000Y40000000D03*\n")
    frame = _layer(tmp_path, "Edge_Cuts.gbr", wide)
    layer = _layer(tmp_path, "F_Cu.gbr", FRONT)
    with pytest.raises(JobError, match="across"):
        build_job(layer, machine, power=0.5, spot_compensation=False,
                  frame=frame, place_mm=(10.0, 10.0))


def test_the_header_names_the_frame(tmp_path, make_machine):
    machine = make_machine(calibration_text=NO_CALIBRATION)
    top, bottom, _frame = _both_sides(tmp_path, machine)
    for job in (top, bottom):
        assert any(note.startswith("frame: x ") for note in job.notes)
    plain = build_job(_layer(tmp_path, "F_Cu2.gbr", FRONT), machine, power=0.5,
                      spot_compensation=False)
    assert any("frame: none" in note for note in plain.notes)
