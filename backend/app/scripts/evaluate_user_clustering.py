"""Read-only parameter probe for a user's SonicDistance clustering."""
import argparse

import hdbscan
import numpy as np
import umap
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Song, User, UserSong
from app.services.brain import _pairwise_distance_matrix, _song_vectors


def run(email: str) -> None:
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        songs = list(
            db.execute(
                select(Song).join(UserSong).where(
                    UserSong.user_id == user.id, Song.feature_vector.is_not(None)
                )
            ).scalars()
        )
    finally:
        db.close()

    matrix = _pairwise_distance_matrix([_song_vectors(song) for song in songs])
    for dimensions in (5, 8):
        embedded = umap.UMAP(
            n_neighbors=15,
            n_components=dimensions,
            metric="precomputed",
            random_state=42,
        ).fit_transform(matrix)
        for method in ("eom", "leaf"):
            for size in (4, 6, 8, 10):
                for samples in (1, 2, 3):
                    labels = hdbscan.HDBSCAN(
                        min_cluster_size=size,
                        min_samples=samples,
                        cluster_selection_method=method,
                    ).fit_predict(embedded)
                    counts = sorted(
                        (int(np.sum(labels == label)) for label in set(labels) if label >= 0),
                        reverse=True,
                    )
                    noise = int(np.sum(labels < 0))
                    print(dimensions, method, size, samples, len(counts), noise, counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    run(args.email)
