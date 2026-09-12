import { Radar } from "lucide-react";

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full min-h-[420px] flex-col items-center justify-center rounded-[var(--r-lg)] bg-[var(--bg-media)] px-8 text-center">
      <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-surface)]">
        <Radar size={20} strokeWidth={1.5} className="text-[var(--text-tertiary)]" />
      </div>
      <h3 className="text-[16px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">{title}</h3>
      <p className="mt-2 max-w-xs text-[13px] leading-[1.6] text-[var(--text-body)]">{body}</p>
    </div>
  );
}
