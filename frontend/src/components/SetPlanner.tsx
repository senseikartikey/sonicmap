"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronLeft, ChevronRight, Download, Loader2, Play, Square, TriangleAlert } from "lucide-react";
import { MashupPlan, PlannedTrack, PlannedTransition, TransitionKind } from "@/lib/types";
import { MixEvent, MixPlayer } from "@/lib/mixPlayer";
import { Button } from "@/components/ui/Button";

/** Each technique gets its own colour so a set reads as a shape before it reads as a list —
 * warm for the energetic moves, cool for the patient ones, red for the honest failure. */
const KIND_STYLE: Record<TransitionKind, { label: string; hint: string; color: string }> = {
  bass_swap: { label: "Bass swap", hint: "Cross the low end on the downbeat", color: "var(--accent)" },
  double_drop: { label: "Double drop", hint: "Land both peaks together", color: "#e0653f" },
  rolling: { label: "Rolling", hint: "Ride the tail into the drop", color: "#d4a03c" },
  long_blend: { label: "Long blend", hint: "Room to let it breathe", color: "#5aa9c9" },
  echo_out: { label: "Echo out", hint: "Escape the key clash", color: "#8f7fc4" },
  cut: { label: "Cut", hint: "Too far apart to hold together", color: "var(--error)" },
};

function duration(ms: number | null | undefined) {
  if (!ms) return null;
  const total = Math.round(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0
    ? `${hours}h ${String(minutes).padStart(2, "0")}m`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** The energy arc, drawn from the tracks as ordered. This is the one view that shows whether
 * the set actually goes anywhere — a flat line means the running order is doing nothing. */
function EnergyCurve({ plan }: { plan: MashupPlan }) {
  const points = useMemo(() => {
    const values = plan.tracks.map((track) => track.energy);
    if (values.length < 2) return null;
    const known = values.filter((value): value is number => value !== null);
    if (known.length < 2) return null;
    const low = Math.min(...known);
    const high = Math.max(...known);
    const span = high - low || 1;
    return values.map((value, index) => ({
      x: (index / (values.length - 1)) * 100,
      y: value === null ? 50 : 100 - ((value - low) / span) * 100,
      known: value !== null,
    }));
  }, [plan.tracks]);

  if (!points) return null;
  const line = points.map((point) => `${point.x},${point.y}`).join(" ");
  return (
    <svg
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      className="h-14 w-full"
      role="img"
      aria-label={`Energy across the set, from ${plan.tracks[0]?.title} to ${plan.tracks[plan.tracks.length - 1]?.title}`}
    >
      <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      {points.map((point, index) => (
        <circle
          key={index}
          cx={point.x}
          cy={point.y}
          r="2"
          fill={point.known ? "var(--accent-bright)" : "var(--text-tertiary)"}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}

function TransitionRow({ transition, expanded, onToggle, audition, playable, nudge, onAudition, onStop, onNudge }: {
  transition: PlannedTransition;
  expanded: boolean;
  onToggle: () => void;
  /** State of *this* transition's audition, if it is the one currently sounding. */
  audition: { state: "loading" | "playing"; note: string; progress: number } | null;
  /** False when either side has no preview clip or no measured tempo — nothing to play. */
  playable: boolean;
  /** Manual alignment correction in beats, if the listener has moved it off the detected grid. */
  nudge: number;
  onAudition: () => void;
  onStop: () => void;
  onNudge: (delta: number) => void;
}) {
  const style = KIND_STYLE[transition.kind];
  const stretch = transition.tempo
    ? `${transition.tempo.stretch_pct >= 0 ? "+" : ""}${transition.tempo.stretch_pct.toFixed(1)}%`
    : null;
  return (
    <div className="relative pl-6">
      {/* The rail is the set's spine — it runs behind every transition so the list reads as
          one continuous mix rather than a stack of unrelated pairs. */}
      <span aria-hidden className="absolute left-[7px] top-0 h-full w-px bg-[var(--border-subtle)]" />
      <span aria-hidden className="absolute left-[3px] top-4 h-2 w-2 rounded-full" style={{ background: style.color }} />
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 py-2.5 text-left transition-colors hover:bg-white/[0.02]"
      >
        <span className="font-mono text-[11px] font-semibold tracking-[0.05em]" style={{ color: style.color }}>
          {style.label.toUpperCase()}
        </span>
        <span className="font-mono text-[11px] tabular-nums text-[var(--text-tertiary)]">
          {transition.bars} bars{transition.seconds ? ` · ${Math.round(transition.seconds)}s` : ""}
        </span>
        {stretch && (
          <span
            className="font-mono text-[11px] tabular-nums"
            style={{ color: transition.tempo?.within_comfort ? "var(--text-tertiary)" : "var(--error)" }}
          >
            {stretch}
          </span>
        )}
        {transition.camelot_from && transition.camelot_to && (
          <span className="font-mono text-[11px] text-[var(--text-tertiary)]">
            {transition.camelot_from}→{transition.camelot_to}
          </span>
        )}
        {transition.warnings.length > 0 && (
          <TriangleAlert size={13} className="text-[var(--error)]" aria-label={`${transition.warnings.length} warnings`} />
        )}
        <ChevronDown
          size={14}
          className={`ml-auto text-[var(--text-tertiary)] transition-transform ${expanded ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {/* Sits outside the disclosure button — a button inside a button is invalid, and this
          has to stay reachable whether or not the detail is open. */}
      <div className="absolute right-7 top-2">
        <button
          type="button"
          disabled={!playable}
          onClick={audition ? onStop : onAudition}
          aria-label={
            playable
              ? audition ? "Stop this transition" : "Hear this transition"
              : "One of these tracks has no measured tempo"
          }
          title={playable ? undefined : "One of these tracks has no measured tempo, so it can't be beat-matched"}
          className="rounded-full p-1.5 text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.08] hover:text-[var(--accent-bright)] disabled:opacity-25 disabled:hover:bg-transparent"
        >
          {audition?.state === "loading" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : audition ? (
            <Square size={14} fill="currentColor" />
          ) : (
            <Play size={14} />
          )}
        </button>
      </div>
      {audition?.state === "playing" && (
        <div className="pb-2 pr-8">
          <div className="h-0.5 w-full overflow-hidden rounded bg-white/[0.08]">
            <div
              className="h-full transition-[width] duration-100 ease-linear"
              style={{ width: `${audition.progress * 100}%`, background: style.color }}
            />
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="text-[11px] text-[var(--text-tertiary)]">{audition.note}</p>
            <div className="flex items-center gap-1">
              <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                Align
              </span>
              <button
                type="button"
                onClick={() => onNudge(-0.25)}
                aria-label="Pull the incoming track earlier by a quarter beat"
                className="rounded p-0.5 text-[var(--text-tertiary)] hover:bg-white/[0.08] hover:text-[var(--text-primary)]"
              >
                <ChevronLeft size={13} />
              </button>
              <span className="w-10 text-center font-mono text-[10px] tabular-nums text-[var(--text-body)]">
                {nudge === 0 ? "on grid" : `${nudge > 0 ? "+" : ""}${nudge}`}
              </span>
              <button
                type="button"
                onClick={() => onNudge(0.25)}
                aria-label="Push the incoming track later by a quarter beat"
                className="rounded p-0.5 text-[var(--text-tertiary)] hover:bg-white/[0.08] hover:text-[var(--text-primary)]"
              >
                <ChevronRight size={13} />
              </button>
            </div>
          </div>
        </div>
      )}
      {expanded && (
        <div className="pb-3 pr-2 text-[13px] leading-relaxed">
          <p className="text-[var(--text-body)]">{style.hint}.</p>
          {transition.notes.map((note) => (
            <p key={note} className="mt-1 text-[var(--text-body)]">{note}</p>
          ))}
          {transition.warnings.map((warning) => (
            <p key={warning} className="mt-1 text-[var(--error)]">{warning}</p>
          ))}
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] tracking-[0.04em] text-[var(--text-tertiary)]">
            {(["tempo", "harmonic", "sonic", "energy"] as const).map((part) => (
              <div key={part} className="flex gap-1">
                <dt className="uppercase">{part}</dt>
                <dd className="tabular-nums text-[var(--text-body)]">{Math.round(transition.score[part] * 100)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

export function SetPlanner({
  plan,
  name,
  quality,
  saved,
  busy,
  onMove,
  onExport,
}: {
  plan: MashupPlan;
  name: string;
  quality: number | null;
  /** Exports need a persisted set, so an unsaved preview offers none rather than a broken button. */
  saved: boolean;
  busy?: boolean;
  onMove: (songId: string, direction: -1 | 1) => void;
  onExport: (format: "rekordbox" | "m3u8" | "cue") => void;
}) {
  const [openTransition, setOpenTransition] = useState<string | null>(null);
  // One player for the whole panel: two decks at a time is the point, and a second AudioContext
  // would let two auditions overlap into noise.
  const playerRef = useRef<MixPlayer | null>(null);
  const frameRef = useRef<number | null>(null);
  const [audition, setAudition] = useState<
    { key: string; state: "loading" | "playing"; note: string; progress: number } | null
  >(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [nudges, setNudges] = useState<Record<string, number>>({});

  useEffect(() => () => {
    // Tear the AudioContext down with the panel — an orphaned one keeps the tab's audio
    // session alive and, on some browsers, blocks the next one from starting.
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    playerRef.current?.close();
    playerRef.current = null;
  }, []);

  function stopAudition() {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    playerRef.current?.stop();
    setAudition(null);
  }

  function audition_(
    from: PlannedTrack,
    to: PlannedTrack,
    transition: PlannedTransition,
    nudgeOverride?: number,
  ) {
    const key = `${transition.from_id}-${transition.to_id}`;
    const nudge = nudgeOverride ?? nudges[key] ?? 0;
    stopAudition();
    setAudioError(null);
    const player = (playerRef.current ??= new MixPlayer());
    void player.playTransition(from, to, transition, (event: MixEvent) => {
      if (event.type === "loading") {
        setAudition({ key, state: "loading", note: "", progress: 0 });
      } else if (event.type === "playing") {
        setAudition({ key, state: "playing", note: event.note, progress: 0 });
        const began = performance.now();
        const tick = () => {
          const elapsed = (performance.now() - began) / 1000;
          const progress = Math.min(1, elapsed / event.duration);
          setAudition((current) => (current?.key === key ? { ...current, progress } : current));
          if (progress < 1) frameRef.current = requestAnimationFrame(tick);
        };
        frameRef.current = requestAnimationFrame(tick);
      } else if (event.type === "ended") {
        stopAudition();
      } else {
        setAudioError(event.message);
        stopAudition();
      }
    }, nudge);
  }

  /** Shift the incoming deck by a quarter beat and immediately replay, so the correction is
   * judged by ear against the same transition rather than from memory. */
  function nudge(from: PlannedTrack, to: PlannedTrack, transition: PlannedTransition, delta: number) {
    const key = `${transition.from_id}-${transition.to_id}`;
    const next = Math.round(((nudges[key] ?? 0) + delta) * 100) / 100;
    setNudges((current) => ({ ...current, [key]: next }));
    audition_(from, to, transition, next);
  }

  const warningCount = plan.transitions.reduce((total, t) => total + t.warnings.length, 0);
  const blendable = plan.transitions.filter((t) => t.kind !== "cut").length;
  const runtime = duration(plan.total_ms);
  // A cue sheet indexes a continuous recording, so it needs every track's length. Without
  // them the server refuses rather than stamping two tracks at the same timestamp — so the
  // button says why instead of offering a download that would come back as an error.
  const missingLengths = plan.tracks.filter((track) => !track.duration_ms).length;

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h3 className="text-[22px] font-semibold tracking-[-0.02em]">{name}</h3>
          <p className="mt-1 font-mono text-[11px] tracking-[0.05em] text-[var(--text-tertiary)] tabular-nums">
            {plan.tracks.length} TRACKS
            {runtime ? ` · ${runtime}` : ""}
            {` · ${blendable}/${plan.transitions.length} BLENDABLE`}
            {quality !== null ? ` · MIX QUALITY ${Math.round(quality * 100)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {(["rekordbox", "m3u8", "cue"] as const).map((format) => {
            const blocked = format === "cue" && missingLengths > 0;
            return (
              <Button
                key={format}
                size="sm"
                variant="secondary"
                disabled={!saved || busy || blocked}
                onClick={() => onExport(format)}
                title={
                  !saved
                    ? "Save this set to export it"
                    : blocked
                      ? `${missingLengths} track${missingLengths === 1 ? "" : "s"} in this set have no known length, so a cue sheet's timings would be wrong`
                      : undefined
                }
              >
                <Download size={13} /> {format === "m3u8" ? "M3U8" : format === "cue" ? "Cue sheet" : "rekordbox"}
              </Button>
            );
          })}
        </div>
      </div>

      <div className="mt-5 rounded-[var(--r-md)] border border-[var(--border-subtle)] px-3 pb-1 pt-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
          Energy · {plan.shape.replace("_", " ")}
        </p>
        <EnergyCurve plan={plan} />
      </div>

      {audioError && <p className="mt-4 text-[13px] text-[var(--error)]">{audioError}</p>}

      {warningCount > 0 && (
        <p className="mt-4 flex items-start gap-2 text-[13px] text-[var(--text-body)]">
          <TriangleAlert size={14} className="mt-0.5 shrink-0 text-[var(--error)]" aria-hidden />
          <span>
            {warningCount} thing{warningCount === 1 ? "" : "s"} to watch in this set — open a transition to
            see what, or reorder the tracks and the blends replan around you.
          </span>
        </p>
      )}

      <ol className="mt-5">
        {plan.tracks.map((track, index) => {
          const transition = plan.transitions[index];
          return (
            <li key={track.id}>
              <div className="flex items-center gap-3 py-2">
                <span className="w-6 shrink-0 font-mono text-[12px] tabular-nums text-[var(--text-tertiary)]">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] text-[var(--text-primary)]">{track.title}</p>
                  <p className="truncate text-[13px] text-[var(--text-tertiary)]">{track.artist}</p>
                </div>
                <div className="hidden shrink-0 items-center gap-3 font-mono text-[11px] tabular-nums text-[var(--text-tertiary)] sm:flex">
                  {track.camelot && <span className="text-[var(--accent)]">{track.camelot}</span>}
                  {track.bpm && <span>{track.bpm.toFixed(0)} BPM</span>}
                  {track.duration_ms && <span>{duration(track.duration_ms)}</span>}
                </div>
                <div className="flex shrink-0 gap-0.5">
                  <button
                    type="button"
                    onClick={() => onMove(track.id, -1)}
                    disabled={index === 0 || busy}
                    aria-label={`Move ${track.title} earlier`}
                    className="rounded p-1.5 text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.06] hover:text-[var(--text-primary)] disabled:opacity-25"
                  >
                    <ArrowUp size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onMove(track.id, 1)}
                    disabled={index === plan.tracks.length - 1 || busy}
                    aria-label={`Move ${track.title} later`}
                    className="rounded p-1.5 text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.06] hover:text-[var(--text-primary)] disabled:opacity-25"
                  >
                    <ArrowDown size={14} />
                  </button>
                </div>
              </div>
              {transition && (
                <TransitionRow
                  transition={transition}
                  expanded={openTransition === `${transition.from_id}-${transition.to_id}`}
                  onToggle={() =>
                    setOpenTransition((current) =>
                      current === `${transition.from_id}-${transition.to_id}`
                        ? null
                        : `${transition.from_id}-${transition.to_id}`
                    )
                  }
                  audition={
                    audition?.key === `${transition.from_id}-${transition.to_id}`
                      ? { state: audition.state, note: audition.note, progress: audition.progress }
                      : null
                  }
                  // Tempo is the real gate: without it there is nothing to beat-match. A missing
                  // preview URL is not — the player asks the backend to re-resolve one, so
                  // disabling on it would hide a transition that plays perfectly well.
                  playable={Boolean(track.bpm && plan.tracks[index + 1]?.bpm)}
                  nudge={nudges[`${transition.from_id}-${transition.to_id}`] ?? 0}
                  onAudition={() => audition_(track, plan.tracks[index + 1], transition)}
                  onStop={stopAudition}
                  onNudge={(delta) => nudge(track, plan.tracks[index + 1], transition, delta)}
                />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
