from app.macro_state import DIMENSIONS, LABELS, blend, state_label, trend_label, weight


def test_weight_bounds():
    assert weight(100, 100, 100) == 0.6
    assert weight(5, 5, 5) == 0.05
    assert 0.05 < weight(70, 70, 80) < 0.6


def test_blend_moves_toward_target_and_clamps():
    assert blend(-45, 1, 80, 0.6) == 30
    assert blend(0, 1, 100, 0.6) == 60
    assert blend(95, 1, 100, 0.6) == 98
    assert blend(-100, -1, 100, 0.6) == -100
    assert blend(40, 0, 50, 0.6) == 34  # neutral evidence decays the score a little


def test_state_labels_cover_every_dimension():
    for region, dims in DIMENSIONS.items():
        for dim in dims:
            assert dim in LABELS, (region, dim)
            assert state_label(dim, 75) == LABELS[dim][0]
            assert state_label(dim, 0) == LABELS[dim][2]
            assert state_label(dim, -75) == LABELS[dim][4]


def test_trend_label():
    assert trend_label(30, [10, 12, 8]) == 'RISING'
    assert trend_label(-5, [10, 12, 8]) == 'FALLING'
    assert trend_label(11, [10, 12, 8]) == 'STABLE'
    assert trend_label(50, []) == 'STABLE'
