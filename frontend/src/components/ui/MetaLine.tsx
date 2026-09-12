import { Song } from "@/lib/types";

function fmt(n: number | null, digits = 0, suffix = ""): string {
  if (n === null) return "—";
  return `${n.toFixed(digits)}${suffix}`;
}

/** Real extracted-feature readout in the world's `//`-separated monospace metadata pattern —
 * unlike the reference's invented technical strings, every field here is a real measurement. */
export function MetaLine({
  song,
  className = "",
  wrap = false,
}: {
  song: Song;
  className?: string;
  /** Allow the metadata line to wrap instead of truncating — for standalone panels where
   * every field must stay readable, as opposed to tight spaces like the map tooltip. */
  wrap?: boolean;
}) {
  if (song.extraction_status !== "extracted") {
    return (
      <p className={`font-mono text-[11px] tracking-[0.04em] text-[var(--text-tertiary)] ${className}`}>
        NO FEATURES YET
      </p>
    );
  }
  const line = [
    song.genre ?? "—",
    fmt(song.bpm, 0, " BPM"),
    song.key ?? "—",
    `ENERGY ${fmt(song.energy, 2)}`,
    `DANCE ${fmt(song.danceability, 2)}`,
  ].join(" // ");

  return (
    <p
      className={`font-mono text-[11px] leading-[1.5] tracking-[0.04em] text-[var(--text-tertiary)] tabular ${
        wrap ? "" : "truncate"
      } ${className}`}
    >
      {line}
    </p>
  );
}
