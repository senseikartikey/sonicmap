# Third-party notices

## StemKit

Stem Studio's product workflow was informed by
[danielravina/stemkit](https://github.com/danielravina/stemkit): acquire authorized audio,
normalize it, separate it with Demucs, and expose synchronized stem playback. The Sonicmap
implementation is a new server-side job/storage architecture rather than the Electron IPC
application. Use and adaptation were authorized in writing by a StemKit development-team
member; that permission record must be retained with Sonicmap's private legal records.

## Runtime components

The worker images include PyTorch, Torchaudio, Demucs, ffmpeg, and optionally yt-dlp, plus
`beat-this` (CPJKU, MIT license) for beat/downbeat tracking. Their upstream licenses and
notices remain applicable and should be included in production image and distribution audits.
YouTube acquisition is disabled by default and does not grant users rights to download or
process third-party recordings.

## Catalog features

`Song.genre_vector` is produced by Essentia's Discogs-EffNet model, which is distributed under
CC BY-NC-SA 4.0 (non-commercial). This is acceptable for a non-commercial/portfolio deployment
of Sonicmap; it would need to be replaced or separately licensed before any commercial use,
since it currently feeds the core `sonic_distance` recommendation signal.
