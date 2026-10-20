"""Axis skew: each row shifts by y · tg θ (plan Ш1, design §17).

Cells closed here: θ = 0 identity; the shift is proportional to the row height;
the sign follows the sign of tg θ; empty rows pass through untouched; a non-finite
tangent is a ValueError. Rotation and scale_y are NOT here — they change the row
height and act on the input, before rasterisation (contracts.md).
"""
import math

import pytest

from gerber2ldi.scan import ScanRow, apply_skew


def test_zero_tangent_is_the_identity():
    """No skew measured, no shift — and the rows come back unchanged."""
    rows = [ScanRow(0.0, ((0.0, 1.0),)), ScanRow(10.0, ((2.0, 3.0),))]
    assert list(apply_skew(rows, 0.0)) == rows


def test_shift_is_proportional_to_the_row_height():
    """x += y · tg θ, with y the row's own height (the origin decides the pivot)."""
    rows = [ScanRow(0.0, ((0.0, 1.0),)), ScanRow(10.0, ((0.0, 1.0),))]
    out = list(apply_skew(rows, 0.01))
    assert out[0].segments == ((0.0, 1.0),)
    assert out[1].segments == ((0.1, 1.1),)


def test_tangent_sign_flips_the_shift():
    """A shear to the other side is the same code with the other sign."""
    row = ScanRow(20.0, ((0.0, 1.0),))
    plus = next(iter(apply_skew([row], math.tan(math.radians(0.5)))))
    minus = next(iter(apply_skew([row], -math.tan(math.radians(0.5)))))
    assert plus.segments[0][0] > 0.0
    assert minus.segments[0][0] < 0.0
    assert plus.segments[0][0] == pytest.approx(-minus.segments[0][0])


def test_empty_rows_are_passed_through():
    """No ink, nothing to shift — the row object itself comes back."""
    row = ScanRow(5.0, ())
    assert next(iter(apply_skew([row], 0.5))) is row


@pytest.mark.parametrize("tangent", [float("nan"), float("inf")])
def test_non_finite_tangent_raises(tangent):
    """A NaN from a broken calibration must not produce NaN coordinates."""
    with pytest.raises(ValueError, match="tangent"):
        list(apply_skew([ScanRow(0.0, ((0.0, 1.0),))], tangent))
