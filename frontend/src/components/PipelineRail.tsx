"use client";

import { LoaderCircle, X } from "lucide-react";
import { Card, SectionLabel } from "@/components/ui/Card";
import { PipelineDot } from "@/components/ui/StatusBadge";
import { MapPoint, pipelineState, PIPELINE_LABEL } from "@/lib/types";

export function PipelineRail({
  points,
  onRemove,
  removingId,
}: {
  points: MapPoint[];
  onRemove: (songId: string) => void;
  removingId: string | null;
}) {
  const pending = points.filter((p) => pipelineState(p) !== "placed");

  if (pending.length === 0) return null;

  return (
    <Card className="p-6">
      <div className="flex items-center justify-between">
        <SectionLabel>Pipeline</SectionLabel>
        <span className="font-mono text-[11px] text-[var(--text-tertiary)] tabular">{pending.length}</span>
      </div>

      <ul className="mt-4 max-h-64 space-y-3 overflow-y-auto pr-1">
        {pending.map((p) => {
          const state = pipelineState(p);
          return (
            <li key={p.song.id} className="flex items-start gap-2.5">
              <div className="mt-1.5">
                <PipelineDot state={state} />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-[var(--text-primary)]">{p.song.title}</p>
                <p className="truncate text-[12px] text-[var(--text-body)]">{p.song.artist}</p>
                <p className="mt-0.5 font-mono text-[10px] tracking-[0.04em] text-[var(--text-tertiary)]">
                  {PIPELINE_LABEL[state].toUpperCase()}
                </p>
              </div>
              <button
                type="button"
                onClick={() => onRemove(p.song.id)}
                disabled={removingId === p.song.id}
                aria-label={`Remove ${p.song.title} from the pipeline`}
                className="mt-0.5 shrink-0 rounded p-1 text-[var(--text-tertiary)] transition-colors hover:bg-[var(--bg-card)] hover:text-[var(--error)] disabled:cursor-wait disabled:opacity-60"
              >
                {removingId === p.song.id ? (
                  <LoaderCircle size={13} strokeWidth={1.5} className="animate-spin" />
                ) : (
                  <X size={13} strokeWidth={1.5} />
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
