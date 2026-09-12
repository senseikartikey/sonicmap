import numpy as np

from app.services.extraction import _coarse_genre_from_predictions


def test_coarse_genre_aggregates_child_probabilities() -> None:
    classes = ["Latin---Reggaeton", "Pop---Ballad", "Pop---Vocal", "Rock---Indie Rock"]
    predictions = np.array([0.30, 0.22, 0.20, 0.10])
    assert _coarse_genre_from_predictions(predictions, classes) == "Pop"


def test_coarse_genre_does_not_reward_parent_with_more_weak_children() -> None:
    classes = ["Pop---Vocal", "Pop---Ballad", "Rock---Style A"] + [
        f"Rock---Weak Style {i}" for i in range(20)
    ]
    predictions = np.array([0.82, 0.76, 0.71] + [0.10] * 20)
    # Summing every child used to return Rock (2.71 total) merely because it has more labels.
    assert _coarse_genre_from_predictions(predictions, classes) == "Pop"


def test_coarse_genre_tie_uses_strongest_prediction() -> None:
    classes = ["Hip Hop---Trap", "Pop---Vocal", "Rock---Indie Rock"]
    predictions = np.array([0.72, 0.61, 0.55])
    assert _coarse_genre_from_predictions(predictions, classes) == "Hip Hop"


def test_coarse_genre_handles_empty_input() -> None:
    assert _coarse_genre_from_predictions(np.array([]), []) is None
