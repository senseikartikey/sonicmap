"use client";

import { useState } from "react";
import { AudioLines, Trash2, X } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { MetaLine } from "@/components/ui/MetaLine";
import { PipelineBadge } from "@/components/ui/StatusBadge";
import { ExternalSongLinks } from "@/components/ui/ExternalSongLinks";
import { MapPoint, pipelineState } from "@/lib/types";

export function SelectedSongPanel({
  point,
  onClose,
  onRemove,
  removing,
  onSeparateStems,
}: {
  point: MapPoint;
  onClose: () => void;
  onRemove: () => void;
  removing: boolean;
  /** Deep-links to Stem Studio with this song's title/artist as context — Stem Studio never
   * auto-fetches audio on a visitor's behalf (that's why source acquisition stays a manual
   * upload/YouTube-link step there), so this hands over context to paste into, not a lookup. */
  onSeparateStems?: () => void;
}) {
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  const state = pipelineState(point);
  const danceabilityUnbounded = point.song.danceability !== null && point.song.danceability > 1;

  return (
    <Card featured className="p-6">
      <div className="flex items-start justify-between gap-3">
        <h3 className="truncate text-[18px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
          {point.song.title}
        </h3>
        <button
          onClick={onClose}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.06] hover:text-[var(--text-primary)]"
          aria-label="Clear selection"
        >
          <X size={14} strokeWidth={1.5} />
        </button>
      </div>

      <p className="truncate text-[14px] text-[var(--text-body)]">{point.song.artist}</p>

      <div className="mt-3 flex items-center justify-between gap-3">
        <PipelineBadge state={state} />
        <ExternalSongLinks title={point.song.title} artist={point.song.artist} size={15} />
      </div>

      {state === "unresolved" && (
        <p className="mt-3 text-[12px] leading-[1.5] text-[var(--text-tertiary)]">
          Couldn&apos;t find this on iTunes, so it has no audio features and won&apos;t appear on
          the map. Try removing it and searching again with the exact artist and title.
        </p>
      )}

      <MetaLine song={point.song} wrap className="mt-4 !text-[12px]" />

      {point.song.styles && (
        <p className="mt-1.5 text-[12px] leading-[1.5] text-[var(--text-tertiary)]">
          {point.song.styles.replaceAll("---", " · ")}
        </p>
      )}

      {danceabilityUnbounded && (
        <p className="mt-1.5 text-[11px] leading-[1.4] text-[var(--text-tertiary)]">
          Dance score is a relative measure, not yet normalized to 0–1 — a value above 1 isn&apos;t
          an error.
        </p>
      )}

      <p className="mt-4 font-mono text-[11px] tracking-[0.04em] text-[var(--text-tertiary)]">
        ADDED VIA {point.source.replace("_", " ").toUpperCase()}
      </p>

      {onSeparateStems && state !== "unresolved" && (
        <button
          onClick={onSeparateStems}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-[var(--r-md)] border border-[var(--border-medium)] px-4 py-2 text-[13px] font-medium text-[var(--text-body)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
        >
          <AudioLines size={13} strokeWidth={1.5} />
          Separate stems
        </button>
      )}

      <button
        onClick={() => (confirmingRemove ? onRemove() : setConfirmingRemove(true))}
        onBlur={() => setConfirmingRemove(false)}
        disabled={removing}
        className={`mt-5 flex w-full items-center justify-center gap-2 rounded-[var(--r-md)] border px-4 py-2 text-[13px] font-medium transition-colors disabled:opacity-50 ${
          confirmingRemove
            ? "border-[var(--error)] bg-[var(--error-subtle)] text-[var(--error)]"
            : "border-[var(--border-medium)] text-[var(--text-body)] hover:border-[var(--error)] hover:text-[var(--error)]"
        }`}
      >
        <Trash2 size={13} strokeWidth={1.5} />
        {removing ? "Removing…" : confirmingRemove ? "Click again to remove" : "Remove from map"}
      </button>
    </Card>
  );
}
