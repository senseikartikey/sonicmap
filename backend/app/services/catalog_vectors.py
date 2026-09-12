"""Builds the ANN-only projection used to shortlist candidates before exact SonicDistance.

This vector is intentionally not the recommendation score. It concatenates normalized
components with square-root weight scaling so L2 search has roughly the same neighborhood
shape as SonicDistance, then brain.score_candidates recomputes the exact metric.
"""
from __future__ import annotations

import math

import numpy as np

from app.models import Song


def _unit(values: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(values))
    return values / norm if norm else np.zeros_like(values)


def build_retrieval_vector(
    feature_vector: list[float] | np.ndarray,
    genre_vector: list[float] | np.ndarray,
) -> list[float]:
    feature = np.asarray(feature_vector, dtype=np.float32)
    genre = np.asarray(genre_vector, dtype=np.float32)
    if feature.shape != (32,) or genre.shape != (1280,):
        raise ValueError("retrieval vectors require 32 acoustic and 1280 genre dimensions")

    # Cosine-distance components are unit-normalized. Euclidean components retain the same
    # normalized source scale extraction.py gives SonicDistance.
    parts = [
        _unit(genre) * math.sqrt(0.45),
        _unit(feature[3:29]) * math.sqrt(0.25),
        feature[0:2] * math.sqrt(0.15),
        feature[29:32] * math.sqrt(0.10),
        feature[2:3] * math.sqrt(0.05),
    ]
    result = np.concatenate(parts)
    if result.shape != (1312,):
        raise AssertionError(f"unexpected retrieval vector shape: {result.shape}")
    return result.astype(float).tolist()


def apply_retrieval_vector(song: Song) -> None:
    if song.feature_vector is None or song.genre_vector is None:
        song.retrieval_vector = None
        return
    song.retrieval_vector = build_retrieval_vector(song.feature_vector, song.genre_vector)
    song.catalog_status = "analyzed"
    song.last_catalog_error = None
