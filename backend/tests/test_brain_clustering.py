import numpy as np

from app.services import brain


def test_clustering_uses_higher_dimensional_taste_space_not_visual_coordinates(monkeypatch):
    distance_matrix = np.array(
        [
            [0.0, 0.1, 0.9, 0.8, 0.85, 0.88],
            [0.1, 0.0, 0.85, 0.82, 0.87, 0.89],
            [0.9, 0.85, 0.0, 0.1, 0.12, 0.11],
            [0.8, 0.82, 0.1, 0.0, 0.09, 0.12],
            [0.85, 0.87, 0.12, 0.09, 0.0, 0.1],
            [0.88, 0.89, 0.11, 0.12, 0.1, 0.0],
        ]
    )
    visual_coords = np.zeros((6, 2))
    observed = {}

    class FakeUmap:
        def __init__(self, **kwargs):
            self.dimensions = kwargs["n_components"]

        def fit_transform(self, matrix):
            np.testing.assert_array_equal(matrix, distance_matrix)
            if self.dimensions == 2:
                return visual_coords
            return np.ones((6, self.dimensions))

    class FakeHdbscan:
        def __init__(self, **kwargs):
            observed["hdbscan_kwargs"] = kwargs

        def fit_predict(self, matrix):
            observed["cluster_input"] = matrix.copy()
            return np.array([0, 0, 1, 1, 1, 1])

    monkeypatch.setattr(brain.umap, "UMAP", FakeUmap)
    monkeypatch.setattr(brain.hdbscan, "HDBSCAN", FakeHdbscan)

    coords, labels = brain._compute_coords_and_labels(distance_matrix, 6)

    np.testing.assert_array_equal(coords, visual_coords)
    np.testing.assert_array_equal(observed["cluster_input"], np.ones((6, 4)))
    assert labels == [0, 0, 1, 1, 1, 1]
    assert observed["hdbscan_kwargs"]["cluster_selection_method"] == "leaf"


def test_cluster_size_grows_slowly_for_product_sized_libraries(monkeypatch):
    observed = {}

    class FakeUmap:
        def __init__(self, **kwargs):
            self.dimensions = kwargs["n_components"]

        def fit_transform(self, matrix):
            return np.zeros((len(matrix), self.dimensions))

    class FakeHdbscan:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def fit_predict(self, matrix):
            return np.zeros(len(matrix), dtype=int)

    monkeypatch.setattr(brain.umap, "UMAP", FakeUmap)
    monkeypatch.setattr(brain.hdbscan, "HDBSCAN", FakeHdbscan)

    matrix = np.zeros((178, 178))
    brain._compute_coords_and_labels(matrix, 178)

    assert observed["min_cluster_size"] == 8
    assert observed["min_samples"] == 2
