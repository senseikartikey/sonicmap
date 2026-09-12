"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Brain, ChevronDown, ChevronLeft, ChevronRight, ListOrdered, Shuffle, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Card, SectionLabel } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ExternalSongLinks } from "@/components/ui/ExternalSongLinks";
import { CatalogStatus, Recommendation } from "@/lib/types";

/** Distance is unbounded feature-space distance, not a percentage — this is a display-only
 * squash (not fed back into ranking) so a visitor without ML background still gets a legible
 * "how close is this" number. 0 distance -> 100%; decays smoothly, never negative. */
function matchPercent(distance: number): number {
  return Math.round(100 * Math.exp(-distance * 1.15));
}

const numberFormatter = new Intl.NumberFormat("en-US");
const PAGE_SIZE = 10;

export function RecommendationsPanel({
  recommendations,
  onGetMore,
  busy,
  exhausted,
  englishOnly,
  onLanguageChange,
  onHide,
  onShowInBrain,
  catalogStatus,
}: {
  recommendations: Recommendation[];
  onGetMore: () => void;
  busy: boolean;
  exhausted: boolean;
  englishOnly: boolean;
  onLanguageChange: (englishOnly: boolean) => void;
  onHide: (songId: string) => void;
  /** Switches to the brain view and pulses the cluster this recommendation actually matched
   * against — takes the *existing* song id it matched, not the recommended song itself. */
  onShowInBrain: (bestMatchSongId: string) => void;
  catalogStatus: CatalogStatus | null;
}) {
  // Which cards are expanded to show the full "why" + feature detail — collapsed by default
  // so the panel reads as a scannable list, not a wall of text per song.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const sortedRecommendations = useMemo(
    () => [...recommendations].sort((a, b) => a.distance - b.distance),
    [recommendations]
  );
  const pageCount = Math.min(3, Math.max(1, Math.ceil(recommendations.length / PAGE_SIZE)));
  const safePage = Math.min(page, pageCount - 1);
  const visibleRecommendations = sortedRecommendations.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  function refreshRecommendations() {
    setPage(0);
    onGetMore();
  }

  function toggleExpanded(songId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(songId)) next.delete(songId);
      else next.add(songId);
      return next;
    });
  }

  async function feedback(r: Recommendation, action: "more_like" | "less_like") {
    await apiFetch("/recommendations/feedback", { method: "POST", body: JSON.stringify({ song_id: r.song.id, best_match_song_id: r.best_match_song_id, action }) });
  }

  return (
    <Card className="p-6">
      <div className="flex items-center justify-between gap-2">
        <SectionLabel>Recommendations</SectionLabel>
        <div className="flex items-center gap-1">
          {/* A set needs at least two tracks to have an order at all, so this only appears
              once there is something to sequence. */}
          {recommendations.length > 1 && (
            <Link
              href="/studio/sets"
              className="inline-flex items-center gap-1.5 rounded-[var(--r-md)] px-2.5 py-1.5 text-[12px] text-[var(--text-body)] transition-colors hover:bg-white/[0.04] hover:text-[var(--text-primary)]"
            >
              <ListOrdered size={13} strokeWidth={1.5} />
              Plan a set
            </Link>
          )}
          {recommendations.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={refreshRecommendations}
              loading={busy}
              disabled={exhausted}
              aria-label="Show different recommendations"
            >
              <Shuffle size={13} strokeWidth={1.5} />
            </Button>
          )}
        </div>
      </div>

      {catalogStatus && (
        <div
          className="mt-3 min-w-0 overflow-hidden rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3"
          role="status"
          aria-live="polite"
        >
          <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
            <p className="min-w-0 text-[10px] font-semibold uppercase leading-[1.35] tracking-[0.07em] text-[var(--text-tertiary)]">
              Brain-ready pool
            </p>
            {catalogStatus.enrichment_active && (
              <span className="inline-flex max-w-full shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--accent)]/20 bg-[var(--accent-subtle)] px-2 py-1 font-mono text-[9px] leading-none tracking-[0.04em] text-[var(--accent)]">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" aria-hidden="true" />
                LIVE
              </span>
            )}
          </div>
          <div className="mt-3 flex items-end gap-2">
            <p className="font-mono text-[24px] leading-none tabular-nums text-[var(--text-primary)]">
              {numberFormatter.format(catalogStatus.analyzed)}
            </p>
            <p className="pb-0.5 text-[10px] leading-none text-[var(--text-tertiary)]">songs analyzed</p>
          </div>
          <div className="mt-3 h-1 overflow-hidden rounded-full bg-[var(--border-subtle)]">
            <div
              className="h-full rounded-full bg-[var(--accent)] transition-[width] duration-700"
              style={{
                width: `${Math.max(0.15, (catalogStatus.analyzed / Math.max(catalogStatus.metadata, 1)) * 100)}%`,
              }}
            />
          </div>
          <p className="mt-2.5 break-words text-[10px] leading-[1.5] text-[var(--text-tertiary)]">
            {numberFormatter.format(catalogStatus.metadata)} discovered
            {catalogStatus.queued > 0
              ? ` · ${numberFormatter.format(catalogStatus.queued)} queued for audio analysis`
              : ""}
          </p>
        </div>
      )}

      <div
        role="radiogroup"
        aria-label="Recommendation language"
        className="mt-3 flex gap-1 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-1"
      >
        <button
          role="radio"
          aria-checked={!englishOnly}
            onClick={() => { setPage(0); onLanguageChange(false); }}
          className={`flex-1 rounded-[calc(var(--r-md)-2px)] px-2 py-1.5 text-[12px] font-medium transition-colors duration-150 ${
            !englishOnly
              ? "bg-[var(--bg-card)] text-[var(--text-primary)] shadow-[inset_0_1px_0_rgba(255,248,230,0.08)]"
              : "text-[var(--text-body)] hover:text-[var(--text-primary)]"
          }`}
        >
          Mixed languages
        </button>
        <button
          role="radio"
          aria-checked={englishOnly}
          onClick={() => { setPage(0); onLanguageChange(true); }}
          className={`flex-1 rounded-[calc(var(--r-md)-2px)] px-2 py-1.5 text-[12px] font-medium transition-colors duration-150 ${
            englishOnly
              ? "bg-[var(--bg-card)] text-[var(--text-primary)] shadow-[inset_0_1px_0_rgba(255,248,230,0.08)]"
              : "text-[var(--text-body)] hover:text-[var(--text-primary)]"
          }`}
        >
          English
        </button>
      </div>

      {recommendations.length === 0 ? (
        <p className="mt-4 text-[13px] leading-[1.6] text-[var(--text-body)]">
          Once your map has a few placed songs, this fills in with the closest matches by
          proximity in feature space.
        </p>
      ) : (
        <>
          <ul className="mt-4 space-y-3">
            {visibleRecommendations.map((r) => {
              const isExpanded = expanded.has(r.song.id);
              return (
                <li key={r.song.id} className="rounded-[var(--r-md)] bg-[var(--bg-surface)] p-3">
                  <p className="break-words text-[13px] font-medium leading-[1.4] text-[var(--text-primary)]">
                    {r.song.title}
                  </p>
                  <div className="mt-1.5 flex items-center justify-between gap-3">
                    <p className="min-w-0 break-words text-[12px] leading-[1.4] text-[var(--text-body)]">{r.song.artist}</p>
                    <ExternalSongLinks title={r.song.title} artist={r.song.artist} size={12} className="shrink-0" />
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] pt-2">
                    <span
                      className="font-mono text-[11px] tabular text-[var(--accent)]"
                      title={`Feature-space distance: ${r.distance.toFixed(3)}`}
                    >
                      {matchPercent(r.distance)}% match
                    </span>
                    <div className="flex shrink-0 items-center gap-3">
                      <button
                        onClick={() => onShowInBrain(r.best_match_song_id)}
                        aria-label={`Show the cluster "${r.song.title}" matched against, in the brain view`}
                        className="text-[var(--text-tertiary)] transition-colors hover:text-[var(--accent)]"
                      >
                        <Brain size={13} strokeWidth={1.5} />
                      </button>
                      <button
                        onClick={() => onHide(r.song.id)}
                        aria-label={`Never recommend "${r.song.title}" again`}
                        className="text-[var(--text-tertiary)] transition-colors hover:text-[var(--error)]"
                      >
                        <X size={13} strokeWidth={1.5} />
                      </button>
                    </div>
                  </div>

                  {isExpanded && (
                    <>
                      <p className="mt-2 text-[12px] leading-[1.5] text-[var(--text-tertiary)]">{r.reason}</p>
                      {r.song.bpm !== null && (
                        <p className="mt-1.5 font-mono text-[11px] tabular text-[var(--text-tertiary)]">
                          {Math.round(r.song.bpm)} BPM
                          {r.song.key ? ` · ${r.song.key}` : ""}
                          {r.song.energy !== null ? ` · energy ${r.song.energy.toFixed(2)}` : ""}
                          {r.song.genre ? ` · ${r.song.genre}` : ""}
                        </p>
                      )}
                      <div className="mt-3 space-y-1.5" aria-label="Recommendation X-Ray">
                        {Object.entries(r.xray ?? {}).sort((a, b) => b[1] - a[1]).map(([name, value]) => (
                          <div key={name} className="grid grid-cols-[58px_1fr] items-center gap-2">
                            <span className="text-[10px] capitalize text-[var(--text-tertiary)]">{name}</span>
                            <div className="h-1 rounded-full bg-[var(--border-subtle)]"><div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${Math.min(100, value * 280)}%` }} /></div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}

                  <div className="mt-2 flex gap-3">
                    <button onClick={() => void feedback(r, "more_like")} className="flex items-center gap-1 text-[10px] text-[var(--text-tertiary)] hover:text-[var(--accent)]"><ThumbsUp size={11} /> More like this</button>
                    <button onClick={() => void feedback(r, "less_like")} className="flex items-center gap-1 text-[10px] text-[var(--text-tertiary)] hover:text-[var(--error)]"><ThumbsDown size={11} /> Less like this</button>
                  </div>

                  <button
                    onClick={() => toggleExpanded(r.song.id)}
                    aria-expanded={isExpanded}
                    className="mt-1.5 flex items-center gap-1 text-[11px] font-medium text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                  >
                    <ChevronDown
                      size={12}
                      strokeWidth={1.5}
                      className={`transition-transform duration-150 ${isExpanded ? "rotate-180" : ""}`}
                    />
                    {isExpanded ? "Show less" : "Why this song"}
                  </button>
                </li>
              );
            })}
          </ul>

          {recommendations.length > PAGE_SIZE && (
            <nav className="mt-4 flex items-center justify-between border-t border-[var(--border-subtle)] pt-3" aria-label="Recommendation pages">
              <button
                onClick={() => setPage((current) => Math.max(0, current - 1))}
                disabled={safePage === 0}
                className="rounded-[var(--r-sm)] p-1.5 text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Previous recommendation page"
              >
                <ChevronLeft size={14} />
              </button>
              <div className="flex items-center gap-1.5">
                {Array.from({ length: pageCount }, (_, index) => (
                  <button
                    key={index}
                    onClick={() => setPage(index)}
                    aria-current={safePage === index ? "page" : undefined}
                    className={`h-7 min-w-7 rounded-[var(--r-sm)] px-2 font-mono text-[11px] transition-colors ${
                      safePage === index
                        ? "bg-[var(--accent)] text-[var(--bg-base)]"
                        : "text-[var(--text-tertiary)] hover:bg-white/[0.05] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    {index + 1}
                  </button>
                ))}
              </div>
              <button
                onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
                disabled={safePage === pageCount - 1}
                className="rounded-[var(--r-sm)] p-1.5 text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Next recommendation page"
              >
                <ChevronRight size={14} />
              </button>
            </nav>
          )}

          {exhausted && (
            <p className="mt-3 text-[12px] leading-[1.5] text-[var(--text-tertiary)]">
              That&apos;s every close match in the catalog right now — add more songs to find new
              ones.
            </p>
          )}
        </>
      )}
    </Card>
  );
}
