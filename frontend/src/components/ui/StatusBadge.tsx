import { PipelineState } from "@/lib/types";

const DOT_CLASS: Record<PipelineState, string> = {
  resolving: "bg-[var(--info)] animate-pulse",
  extracting: "bg-[var(--accent)] animate-pulse",
  unresolved: "bg-[var(--error)]",
  extraction_failed: "bg-[var(--error)]",
  unplaced: "bg-[var(--text-tertiary)]",
  placed: "bg-[var(--success)]",
};

const LABEL: Record<PipelineState, string> = {
  resolving: "RESOLVING",
  extracting: "EXTRACTING",
  unresolved: "UNRESOLVED",
  extraction_failed: "EXTRACTION FAILED",
  unplaced: "READY",
  placed: "PLACED",
};

export function PipelineDot({ state }: { state: PipelineState }) {
  return <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${DOT_CLASS[state]}`} aria-hidden />;
}

export function PipelineBadge({ state }: { state: PipelineState }) {
  return (
    <span className="inline-flex items-center gap-[6px] font-mono text-[11px] tracking-[0.04em] text-[var(--text-tertiary)]">
      <PipelineDot state={state} />
      {LABEL[state]}
    </span>
  );
}

export function SystemStatus({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-[8px] rounded-[var(--r-pill)] border border-[var(--border-subtle)] bg-white/[0.02] px-3 py-1.5 font-mono text-[11px] tracking-[0.04em]">
      <span
        className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-[var(--success)]" : "bg-[var(--accent)] animate-pulse"}`}
        aria-hidden
      />
      <span className={ok ? "text-[var(--success)]" : "text-[var(--accent)]"}>{label}</span>
    </span>
  );
}
