import io
import tarfile

import zstandard

from app.scripts.import_musicbrainz_catalog import _open_text


def test_open_text_streams_canonical_csv_from_official_tar_zst_shape(tmp_path) -> None:
    csv_bytes = b"recording_mbid,recording_name,artist_credit_name,score\nabc,Song,Artist,1\n"
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        info = tarfile.TarInfo("dump/canonical_musicbrainz_data.csv")
        info.size = len(csv_bytes)
        archive.addfile(info, io.BytesIO(csv_bytes))
    archive_path = tmp_path / "canonical.tar.zst"
    archive_path.write_bytes(zstandard.ZstdCompressor().compress(tar_buffer.getvalue()))

    with _open_text(archive_path) as handle:
        assert handle.readline().startswith("recording_mbid")
        assert handle.readline().strip() == "abc,Song,Artist,1"
