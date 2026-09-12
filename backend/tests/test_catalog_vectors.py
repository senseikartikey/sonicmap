import numpy as np
import pytest

from app.services.catalog_vectors import build_retrieval_vector


def test_retrieval_vector_has_index_dimension_and_is_finite() -> None:
    feature = np.linspace(0.0, 1.0, 32)
    genre = np.linspace(-1.0, 1.0, 1280)
    result = np.asarray(build_retrieval_vector(feature, genre))
    assert result.shape == (1312,)
    assert np.isfinite(result).all()


def test_retrieval_vector_rejects_incompatible_extractor_shapes() -> None:
    with pytest.raises(ValueError):
        build_retrieval_vector(np.zeros(31), np.zeros(1280))
    with pytest.raises(ValueError):
        build_retrieval_vector(np.zeros(32), np.zeros(1279))


def test_retrieval_vector_preserves_component_boundaries() -> None:
    feature = np.zeros(32)
    feature[0] = 1.0
    feature[3] = 1.0
    genre = np.zeros(1280)
    genre[7] = 1.0
    result = np.asarray(build_retrieval_vector(feature, genre))
    assert result[7] > 0  # genre block
    assert result[1280] > 0  # MFCC block
    assert result[1306] > 0  # rhythm block
    assert np.count_nonzero(result) == 3
