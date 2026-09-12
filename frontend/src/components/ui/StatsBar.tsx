import { MapPoint, pipelineState } from "@/lib/types";

type Stat = { label: string; value: string };

function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/** Client-side only — every number here is derived from mapPoints already in memory, no
 * extra API call. A quick "your taste, by the numbers" read of the current map, not a
 * separate analytics feature. */
function computeStats(points: MapPoint[]): Stat[] {
  const placed = points.filter((p) => pipelineState(p) === "placed");
  const bpm = mean(placed.map((p) => p.song.bpm).filter((v): v is number => v !== null));
  const energy = mean(placed.map((p) => p.song.energy).filter((v): v is number => v !== null));
  const dance = mean(placed.map((p) => p.song.danceability).filter((v): v is number => v !== null));
  const clusters = new Set(
    placed.map((p) => p.cluster_label).filter((c): c is number => c !== null && c >= 0)
  ).size;
  const genres = new Set(placed.map((p) => p.song.genre).filter((g): g is string => !!g)).size;

  return [
    { label: "Songs placed", value: String(placed.length) },
    { label: "Avg tempo", value: bpm !== null ? `${Math.round(bpm)} BPM` : "—" },
    { label: "Avg energy", value: energy !== null ? energy.toFixed(2) : "—" },
    { label: "Avg dance", value: dance !== null ? dance.toFixed(2) : "—" },
    { label: "Clusters", value: clusters > 0 ? String(clusters) : "—" },
    { label: "Genres", value: genres > 0 ? String(genres) : "—" },
  ];
}

export function StatsBar({ points }: { points: MapPoint[] }) {
  const stats = computeStats(points);
  return (
    <div
      role="group"
      aria-label="Taste map statistics"
      className="grid grid-cols-3 gap-px overflow-hidden rounded-[var(--r-lg)] bg-[var(--border-subtle)] shadow-[inset_0_1px_0_rgba(255,248,230,0.08),0_4px_24px_rgba(0,0,0,0.45)] sm:grid-cols-6"
    >
      {stats.map((s) => (
        <div key={s.label} className="bg-[var(--bg-card)] px-4 py-3">
          <p className="font-mono text-[18px] font-medium tabular text-[var(--text-primary)]">{s.value}</p>
          <p className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.06em] text-[var(--text-tertiary)]">
            {s.label}
          </p>
        </div>
      ))}
    </div>
  );
}
