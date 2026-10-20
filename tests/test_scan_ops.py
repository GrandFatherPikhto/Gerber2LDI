"""Operations on one row (plan Ш1).

The operations are checked against a REFERENCE ORACLE, not against hand-written
expected tuples: every input is sampled into a boolean "is there ink at x" vector
on a 1 µm grid, the result is sampled the same way, and the comparison is the
plain boolean combination. A hand-written tuple would only repeat the code's own
mistake; the oracle states the property ("union is or, subtract is and-not,
intersect is and") once.

Cells: for each operation — both empty, a only, b only, disjoint, touching,
overlapping, nested, nested reversed, identical, and a multi-segment pair.
Empty cells and why: no cell with NaN/inf input — those are rejected by
`normalize`'s caller (the input layer, Ш2); no cell for reversed input per
operation — one table row covers "any operation rejects a reversed segment".
"""
import pytest

from gerber2ldi.scan import (intersect, invert, mirror, normalize, scale, shift,
                             shrink, subtract, union)

STEP_MM = 0.001
XS = [round(i * STEP_MM, 6) for i in range(0, 6001)]  # 0 … 6 mm

PAIRS = [
    ("both empty", (), ()),
    ("a only", ((1.0, 2.0),), ()),
    ("b only", (), ((1.0, 2.0),)),
    ("disjoint", ((0.5, 1.0),), ((2.0, 3.0),)),
    ("touching", ((0.5, 1.0),), ((1.0, 2.0),)),
    ("overlapping", ((0.5, 2.0),), ((1.0, 3.0),)),
    ("nested", ((0.5, 3.0),), ((1.0, 2.0),)),
    ("nested reversed", ((1.0, 2.0),), ((0.5, 3.0),)),
    ("identical", ((1.0, 2.0),), ((1.0, 2.0),)),
    ("multi", ((0.5, 1.0), (2.0, 2.5)), ((0.75, 2.2),)),
]


def _ink(segments):
    """Sample the row: True where there is ink at that x."""
    return [any(x0 <= x < x1 for x0, x1 in segments) for x in XS]


OPERATIONS = [
    ("union", union, lambda a, b: a or b),
    ("subtract", subtract, lambda a, b: a and not b),
    ("intersect", intersect, lambda a, b: a and b),
]


@pytest.mark.parametrize("op_name, op, combine", OPERATIONS,
                         ids=[o[0] for o in OPERATIONS])
@pytest.mark.parametrize("pair_name, a, b", PAIRS, ids=[p[0] for p in PAIRS])
def test_binary_operation_matches_pointwise_oracle(op_name, op, combine,
                                                   pair_name, a, b):
    """The result carries ink exactly where the boolean combination says."""
    got = _ink(op(a, b))
    expected = [combine(ia, ib) for ia, ib in zip(_ink(a), _ink(b))]
    assert got == expected


def test_union_is_commutative_and_idempotent():
    """Two properties the oracle alone would not pin: order and repetition."""
    assert union(((0.0, 1.0),), ((0.5, 2.0),)) == union(((0.5, 2.0),), ((0.0, 1.0),))
    assert union(((0.0, 1.0),), ((0.0, 1.0),)) == ((0.0, 1.0),)


@pytest.mark.parametrize("segments", [
    (),
    ((0.0, 1.0),),
    ((0.0, 1.0), (2.0, 3.0)),
    ((-2.5, -1.0), (0.0, 0.5)),
])
def test_double_mirror_is_identity(segments):
    """Mirror twice about the same axis returns the original, order included."""
    assert mirror(mirror(segments, axis_x=1.25), axis_x=1.25) == normalize(segments)


def test_mirror_keeps_the_number_of_segments():
    """The naive (2c-x0, 2c-x1) would produce reversed segments and, if they were
    dropped, a bottom layer that is silently empty. Count is the cheap witness."""
    segments = ((0.0, 1.0), (2.0, 3.0), (5.0, 5.5))
    assert len(mirror(segments, axis_x=0.0)) == len(segments)


def test_mirror_flips_pair_order():
    """Explicit: one segment about x=0 lands at (-1, 0) and still x0 < x1."""
    assert mirror(((0.0, 1.0),), axis_x=0.0) == ((-1.0, 0.0),)


def test_double_invert_is_identity():
    """Polarity flipped twice is the original figure."""
    segments = ((0.5, 1.0), (2.0, 2.5))
    assert invert(invert(segments, 0.0, 3.0), 0.0, 3.0) == normalize(segments)


@pytest.mark.parametrize("segments, xmin, xmax, expected", [
    ((), 0.0, 2.0, ((0.0, 2.0),)),
    (((0.0, 2.0),), 0.0, 2.0, ()),
    (((0.0, 1.0),), 0.0, 2.0, ((1.0, 2.0),)),
    (((0.5, 1.0),), 0.0, 2.0, ((0.0, 0.5), (1.0, 2.0))),
    (((0.0, 0.5),), 0.0, 2.0, ((0.5, 2.0),)),
    (((5.0, 6.0),), 0.0, 2.0, ((0.0, 2.0),)),
], ids=["empty", "full", "left part", "hole in middle", "touches bounds",
        "outside bounds"])
def test_invert_table(segments, xmin, xmax, expected):
    """Polarity inside explicit bounds; everything outside them is ignored."""
    assert invert(segments, xmin, xmax) == expected


def test_invert_rejects_reversed_bounds():
    """A reversed window is a caller error, not an empty result."""
    with pytest.raises(ValueError, match="reversed"):
        invert((), 2.0, 0.0)


@pytest.mark.parametrize("segments, d, expected", [
    ((), 0.1, ()),
    (((0.0, 1.0),), 0.0, ((0.0, 1.0),)),
    (((0.0, 1.0),), 0.1, ((0.1, 0.9),)),
    (((0.0, 1.0),), 0.5, ()),
    (((0.0, 1.0),), 0.49, ((0.49, 0.51),)),
    (((0.0, 0.1), (1.0, 2.0), (3.0, 3.05)), 0.05, ((1.05, 1.95),)),
], ids=["empty", "zero", "narrow", "exactly nothing left", "just survives",
        "two of three vanish"])
def test_shrink_table(segments, d, expected):
    """Narrowing by d on both sides; a segment of exactly 2d is gone, not zero-length."""
    assert shrink(segments, d) == expected


def test_shrink_rejects_negative_distance():
    """Negative narrowing is a mirror or an error; here it is an error."""
    with pytest.raises(ValueError, match="non-negative"):
        shrink(((0.0, 1.0),), -0.1)


def test_shift_moves_both_ends():
    """Shift never changes a width, only a position."""
    assert shift(((0.0, 1.0), (2.0, 3.0)), 0.5) == ((0.5, 1.5), (2.5, 3.5))
    assert shift(((0.0, 1.0),), 0.0) == ((0.0, 1.0),)


def test_scale_is_about_the_origin():
    """scale_x multiplies X about 0: the measured axis calibration (design §17)."""
    assert scale(((1.0, 2.0),), 2.0) == ((2.0, 4.0),)
    assert scale(((1.0, 2.0),), 0.5) == ((0.5, 1.0),)
    assert scale(((1.0, 2.0),), 1.0) == ((1.0, 2.0),)


@pytest.mark.parametrize("factor", [0.0, -1.0, float("nan")])
def test_scale_rejects_useless_factors(factor):
    """Zero/negative/NaN is a broken profile; mirroring is a separate operation."""
    with pytest.raises(ValueError, match="positive"):
        scale(((1.0, 2.0),), factor)


@pytest.mark.parametrize("call", [
    lambda: union(((2.0, 1.0),), ()),
    lambda: subtract(((2.0, 1.0),), ()),
    lambda: intersect((), ((2.0, 1.0),)),
    lambda: shift(((2.0, 1.0),), 0.0),
    lambda: mirror(((2.0, 1.0),), 0.0),
    lambda: invert(((2.0, 1.0),), 0.0, 3.0),
    lambda: shrink(((2.0, 1.0),), 0.0),
    lambda: scale(((2.0, 1.0),), 2.0),
], ids=["union", "subtract", "intersect", "shift", "mirror", "invert", "shrink",
        "scale"])
def test_every_operation_rejects_a_reversed_segment(call):
    """One row covers all eight: no operation is allowed to swallow the error."""
    with pytest.raises(ValueError, match="reversed segment"):
        call()
