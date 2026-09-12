"""Getting a planned set out of Sonicmap and into the software a DJ already owns.

This is the half of the mashup engine that ships first, and it is deliberately the half that
touches no audio. Sonicmap never holds the files — rekordbox, Serato and Traktor all analyse
the DJ's own library — so what crosses the boundary is the *plan*: the running order, the
blend technique for each pair, the tempo pull and the key move. The DJ opens it against the
tracks they already licensed.

Three formats, because DJs do not share one:
  * rekordbox XML — Pioneer's interchange format, also imported by Serato, Denon Engine and
    VirtualDJ, so it is the widest single target. Carries tempo and key per track and a
    memory cue at each blend point.
  * M3U8 — a plain ordered playlist that opens anywhere at all.
  * CUE sheet — the format for a continuous recorded mix, with an index per track.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote
from xml.etree import ElementTree as ET

from app.services.mashup import SetPlan, Transition, fold_tempo


@dataclass(frozen=True)
class Export:
    filename: str
    media_type: str
    body: str


def _cue_comment(transition: Transition) -> str:
    """One line a DJ can read mid-set, in the order they need it: what to do, then how long,
    then the two numbers they have to dial in."""
    parts = [transition.kind.replace("_", " ").upper(), f"{transition.bars} bars"]
    if transition.tempo and transition.tempo.within_comfort:
        parts.append(f"{transition.tempo.stretch_pct:+.1f}%")
    elif transition.tempo:
        # The technique already says CUT; what the DJ still needs is the size of the gap.
        parts.append(f"{transition.tempo.stretch_pct:+.1f}% - too far to stretch")
    if transition.camelot_from and transition.camelot_to:
        parts.append(f"{transition.camelot_from}>{transition.camelot_to}")
    return " / ".join(parts)


class IncompleteSetError(Exception):
    """Raised rather than exporting timings that would be fiction."""


def _seconds_into_set(plan: SetPlan) -> list[float]:
    """Start offset of each track in a continuously-mixed rendering of the set.

    Each blend overlaps two tracks, so a track starts before the previous one has finished —
    subtracting the overlap is what makes a cue sheet line up with a recorded mix instead of
    drifting further out with every transition.

    Every track length has to be known. Treating an unknown length as zero does not degrade
    gracefully: the offset stops advancing, and two tracks get stamped at the same index,
    which is a broken cue sheet rather than an approximate one.
    """
    missing = [track.title for track in plan.order if not track.duration_ms]
    if missing:
        raise IncompleteSetError(
            "A cue sheet needs every track's length, and "
            f"{len(missing)} in this set {'has' if len(missing) == 1 else 'have'} none yet "
            f"(starting with \"{missing[0]}\"). The rekordbox and M3U8 exports work regardless."
        )
    offsets = [0.0]
    for index, track in enumerate(plan.order[:-1]):
        length = (track.duration_ms or 0) / 1000.0
        overlap = plan.transitions[index].seconds_at(track.bpm) or 0.0
        offsets.append(offsets[-1] + max(0.0, length - overlap))
    return offsets


def to_rekordbox_xml(plan: SetPlan, name: str) -> Export:
    """rekordbox collection XML with the set as an ordered playlist.

    Location is written as a bare `file://localhost/` URI built from title and artist. Every
    DJ tool resolves these against its own library rather than trusting the path, and Sonicmap
    genuinely does not know where the DJ keeps their files — so this is an honest placeholder,
    not a broken path pretending to be real.
    """
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    ET.SubElement(root, "PRODUCT", {"Name": "Sonicmap", "Version": "1.0", "Company": "Sonicmap"})
    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(plan.order))})

    for index, track in enumerate(plan.order, start=1):
        tempo = fold_tempo(track.bpm)
        attrs = {
            "TrackID": str(index),
            "Name": track.title,
            "Artist": track.artist,
            # ElementTree escapes attribute values itself — escaping here as well produced
            # "&amp;amp;" in every artist with an ampersand.
            "Location": f"file://localhost/{quote(track.artist)}%20-%20{quote(track.title)}.mp3",
        }
        # Omitted rather than written as 0 when unknown: a DJ tool reads TotalTime as fact, and
        # a zero length is a worse answer than no answer.
        if track.duration_ms:
            attrs["TotalTime"] = str(int(track.duration_ms / 1000))
        if tempo:
            attrs["AverageBpm"] = f"{tempo:.2f}"
        if track.camelot:
            attrs["Tonality"] = track.camelot
        if track.genre:
            attrs["Genre"] = track.genre
        node = ET.SubElement(collection, "TRACK", attrs)
        # The outgoing side of each blend becomes a memory cue, so the DJ sees where the plan
        # says to start the mix the moment they load the track.
        if index <= len(plan.transitions):
            transition = plan.transitions[index - 1]
            ET.SubElement(node, "POSITION_MARK", {
                "Name": _cue_comment(transition),
                "Type": "0", "Start": "0.000", "Num": "0",
            })

    playlists = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"})
    set_node = ET.SubElement(root_node, "NODE", {
        "Name": name, "Type": "1", "KeyType": "0", "Entries": str(len(plan.order)),
    })
    for index in range(1, len(plan.order) + 1):
        ET.SubElement(set_node, "TRACK", {"Key": str(index)})

    body = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    return Export(filename=f"{_slug(name)}.xml", media_type="application/xml", body=body)


def to_m3u8(plan: SetPlan, name: str) -> Export:
    """Ordered playlist with the blend instructions carried as comments — invisible to a
    player, readable by the DJ who opens the file in a text editor."""
    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
    for index, track in enumerate(plan.order):
        seconds = int((track.duration_ms or 0) / 1000)
        lines.append(f"#EXTINF:{seconds},{track.artist} - {track.title}")
        tempo = fold_tempo(track.bpm)
        detail = " | ".join(filter(None, [
            f"{tempo:g} BPM" if tempo else None,
            track.camelot,
            f"energy {track.energy:.2f}" if track.energy is not None else None,
        ]))
        if detail:
            lines.append(f"# {detail}")
        lines.append(f"{track.artist} - {track.title}.mp3")
        if index < len(plan.transitions):
            lines.append(f"# >> {_cue_comment(plan.transitions[index])}")
    return Export(filename=f"{_slug(name)}.m3u8", media_type="audio/x-mpegurl", body="\n".join(lines) + "\n")


def to_cue_sheet(plan: SetPlan, name: str) -> Export:
    """CUE sheet for the set rendered as one continuous mix."""
    offsets = _seconds_into_set(plan)
    lines = [f'TITLE "{_quote(name)}"', 'PERFORMER "Sonicmap"', f'FILE "{_slug(name)}.wav" WAVE']
    for index, track in enumerate(plan.order):
        total = offsets[index]
        minutes, seconds = divmod(total, 60)
        frames = int((seconds - int(seconds)) * 75)
        lines += [
            f"  TRACK {index + 1:02d} AUDIO",
            f'    TITLE "{_quote(track.title)}"',
            f'    PERFORMER "{_quote(track.artist)}"',
            f"    INDEX 01 {int(minutes):02d}:{int(seconds):02d}:{frames:02d}",
        ]
        if index < len(plan.transitions):
            lines.append(f"    REM TRANSITION {_cue_comment(plan.transitions[index])}")
    return Export(filename=f"{_slug(name)}.cue", media_type="application/x-cue", body="\n".join(lines) + "\n")


def _quote(value: str) -> str:
    return value.replace('"', "'")


def _slug(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_ " else "" for char in value).strip()
    return (cleaned.replace(" ", "-").lower() or "sonicmap-set")[:80]


EXPORTERS = {"rekordbox": to_rekordbox_xml, "m3u8": to_m3u8, "cue": to_cue_sheet}
