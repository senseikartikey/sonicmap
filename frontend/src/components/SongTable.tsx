"use client";

import { MapPoint, pipelineState, PIPELINE_LABEL } from "@/lib/types";
import { PipelineDot } from "@/components/ui/StatusBadge";
import { ExternalSongLinks } from "@/components/ui/ExternalSongLinks";

function fmt(n: number | null, digits = 0): string {
  return n === null ? "—" : n.toFixed(digits);
}

/** Non-spatial equivalent of the map — every song, its status, and its real features as a
 * table, for anyone who can't or doesn't want to use the spatial view. */
export function SongTable({
  points,
  selectedId,
  onSelect,
}: {
  points: MapPoint[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  return (
    <div className="relative overflow-hidden rounded-[var(--r-lg)] bg-[var(--bg-card)] shadow-[inset_0_1px_0_rgba(255,248,230,0.08),0_4px_24px_rgba(0,0,0,0.45)]">
      <div className="overflow-x-auto">
      <table className="w-full min-w-[800px] border-collapse text-left">
        <thead>
          <tr className="border-b border-[var(--border-subtle)]">
            {["Song", "Genre", "Status", "BPM", "Key", "Energy", "Dance", "Cluster", "Listen"].map((h) => (
              <th
                key={h}
                className="px-4 py-3 font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-[var(--text-tertiary)]"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {points.map((p) => {
            const state = pipelineState(p);
            const isSelected = selectedId === p.song.id;
            return (
              <tr
                key={p.song.id}
                onClick={() => onSelect(isSelected ? null : p.song.id)}
                tabIndex={0}
                aria-selected={isSelected}
                aria-label={`${p.song.title} by ${p.song.artist}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(isSelected ? null : p.song.id);
                  }
                }}
                className={`cursor-pointer border-b border-[var(--border-subtle)] transition-colors last:border-b-0 hover:bg-white/[0.03] focus-visible:bg-white/[0.03] ${
                  isSelected ? "bg-[var(--accent-subtle)]" : ""
                }`}
              >
                <td className="max-w-[260px] px-4 py-3">
                  <p className="truncate text-[13px] text-[var(--text-primary)]">{p.song.title}</p>
                  <p className="truncate text-[12px] text-[var(--text-body)]">{p.song.artist}</p>
                </td>
                <td className="max-w-[140px] truncate px-4 py-3 text-[12px] text-[var(--text-body)]">
                  {p.song.genre ?? "—"}
                </td>
                <td className="px-4 py-3">
                  <span className="inline-flex items-center gap-[6px] font-mono text-[11px] text-[var(--text-tertiary)]">
                    <PipelineDot state={state} />
                    {PIPELINE_LABEL[state]}
                  </span>
                </td>
                <td className="px-4 py-3 font-mono text-[12px] tabular text-[var(--text-body)]">{fmt(p.song.bpm)}</td>
                <td className="px-4 py-3 font-mono text-[12px] text-[var(--text-body)]">{p.song.key ?? "—"}</td>
                <td className="px-4 py-3 font-mono text-[12px] tabular text-[var(--text-body)]">{fmt(p.song.energy, 2)}</td>
                <td className="px-4 py-3 font-mono text-[12px] tabular text-[var(--text-body)]">
                  {fmt(p.song.danceability, 2)}
                </td>
                <td className="px-4 py-3 font-mono text-[12px] tabular text-[var(--text-body)]">
                  {p.cluster_label !== null && p.cluster_label >= 0 ? p.cluster_label : "—"}
                </td>
                <td className="px-4 py-3">
                  <ExternalSongLinks title={p.song.title} artist={p.song.artist} size={13} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
      <div
        className="pointer-events-none absolute inset-y-0 right-0 w-8 sm:hidden"
        style={{ background: "linear-gradient(to right, transparent, var(--bg-card))" }}
        aria-hidden
      />
    </div>
  );
}
