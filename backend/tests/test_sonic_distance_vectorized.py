import numpy as np

from app.services.brain import _cross_distance_matrix, sonic_distance


def test_cross_distance_matches_scalar_sonic_distance() -> None:
    rng = np.random.default_rng(42)
    left = [(rng.normal(size=32), rng.normal(size=1280)), (rng.normal(size=32), None)]
    right = [
        (rng.normal(size=32), rng.normal(size=1280)),
        (rng.normal(size=32), None),
        (rng.normal(size=32), rng.normal(size=1280)),
    ]
    actual = _cross_distance_matrix(left, right)
    expected = np.array(
        [[sonic_distance(lf, lg, rf, rg) for rf, rg in right] for lf, lg in left]
    )
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
