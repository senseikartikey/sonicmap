/** Deciding *where* in two clips a blend happens.
 *
 * Pulled out of the player and kept pure — no AudioContext, no buffers, just grids and numbers —
 * because this is the one calculation that decides whether a mix sounds deliberate or sounds
 * like a fade at a random moment, and it needs to be testable on its own.
 *
 * The rule it enforces: when the incoming deck enters, both decks are sitting exactly on a bar
 * line. Beat-matching alone is not enough. Two tracks locked to the same tempo but not the same
 * bar sit a half-bar apart half the time, so one track's snare lands on the other's kick — which
 * is what "no rhythm, it just fades at random" actually is.
 */
import { BeatGrid, beatAtOrBefore, secondsPerBar as barSeconds } from "@/lib/beatGrid";

export const LEAD_IN_BARS = 4;
export const TAIL_BARS = 2;
export const BEATS_PER_BAR = 4;
/** Bars per phrase. Blends begin on a phrase start so they land where a section starts. */
export const PHRASE_BARS = 4;
/** Keeps a blend from running off the end of a buffer mid-fade. */
const SAFETY_SECONDS = 0.25;

export type Alignment = {
  /** Offset into the outgoing clip where playback begins. */
  outgoingStart: number;
  /** Seconds from `outgoingStart` until the incoming deck enters. */
  leadIn: number;
  /** Seconds the two decks overlap. */
  blend: number;
  blendBars: number;
  /** Offset into the incoming clip to start from — a bar line whenever one is known. */
  incomingCue: number;
  tail: number;
  /** True when both grids had trustworthy downbeats and the blend is genuinely bar-locked. */
  barsKnown: boolean;
};

export type ClipInfo = { grid: BeatGrid; duration: number };

/**
 * Work out the blend geometry for one transition.
 *
 * `rate` is the incoming deck's playback rate, so incoming times are expressed in outgoing
 * seconds. `wantedBars` is what the plan asked for; the result may be shorter, because a
 * 32-bar blend does not fit inside two 30-second clips.
 */
export function planAlignment(
  outgoing: ClipInfo,
  incoming: ClipInfo,
  { wantedBars, rate }: { wantedBars: number; rate: number },
): Alignment {
  const secondsPerBeat = outgoing.grid.secondsPerBeat;
  // From the outgoing track's own bar lines rather than assumed to be four beats: a track read
  // in 3 has a shorter bar, and a blend measured in the wrong bar length drifts out of the
  // phrase it was meant to land on.
  const secondsPerBar =
    outgoing.grid.downbeats.length >= 2 ? barSeconds(outgoing.grid) : secondsPerBeat * BEATS_PER_BAR;
  const barsKnown = outgoing.grid.downbeats.length >= 2 && incoming.grid.downbeats.length >= 1;

  const usable = outgoing.duration - outgoing.grid.firstBeat - SAFETY_SECONDS;
  const incomingUsable = (incoming.duration - incoming.grid.firstBeat - SAFETY_SECONDS) / rate;

  let outgoingStart: number;
  let leadIn: number;
  let blend: number;
  let blendBars: number;

  if (barsKnown) {
    // Every position below is read straight out of the downbeat list rather than derived by
    // multiplying a bar length.
    //
    // That distinction is not pedantic. Computing the blend point as
    // `start + leadBars * medianBarLength` assumes every bar is identical, and real grids drift.
    // Checked against stored grids, that arithmetic put most pairs 20-40 ms off a true bar line
    // and one pair 1340 ms off — over half a bar. Indexing is exact by construction and absorbs
    // tempo drift for free.
    const bars = outgoing.grid.downbeats;
    const limit = outgoing.duration - SAFETY_SECONDS;
    const incomingBars = Math.floor(
      (incoming.duration - incoming.grid.downbeats[0] - SAFETY_SECONDS) / (secondsPerBar / rate),
    );

    // The latest phrase start that still leaves room for lead-in, blend and tail: the closest a
    // 30-second clip gets to mixing out of the end of a section.
    let chosen = -1;
    for (let index = 0; index < bars.length; index += PHRASE_BARS) {
      const need = index + LEAD_IN_BARS + wantedBars;
      if (need < bars.length && bars[need] <= limit) chosen = index;
    }
    if (chosen < 0) chosen = 0;

    const leadIndex = Math.min(chosen + LEAD_IN_BARS, bars.length - 1);
    const roomAfterLead = bars.length - 1 - leadIndex;
    const endIndex = leadIndex + Math.max(1, Math.min(wantedBars, roomAfterLead, Math.max(1, incomingBars - TAIL_BARS)));
    const clampedEnd = Math.min(endIndex, bars.length - 1);

    outgoingStart = bars[chosen];
    leadIn = bars[leadIndex] - outgoingStart;
    blend = Math.max(0.2, bars[clampedEnd] - bars[leadIndex]);
    blendBars = Math.max(1, clampedEnd - leadIndex);
  } else {
    const fits = Math.floor(
      Math.min(usable / secondsPerBar - LEAD_IN_BARS, incomingUsable / secondsPerBar - TAIL_BARS),
    );
    blendBars = Math.max(1, Math.min(wantedBars, fits));
    leadIn = Math.min(LEAD_IN_BARS, Math.max(1, Math.floor(usable / secondsPerBar) - blendBars)) * secondsPerBar;
    blend = blendBars * secondsPerBar;
    const latestStart = Math.max(
      outgoing.grid.firstBeat,
      outgoing.duration - SAFETY_SECONDS - leadIn - blend,
    );
    outgoingStart = beatAtOrBefore(outgoing.grid, latestStart);
  }

  return {
    outgoingStart,
    leadIn,
    blend,
    blendBars,
    // The incoming deck enters on one of *its* bar lines, so its bar 1 coincides with a bar 1 of
    // the outgoing deck. Together with the indexed lead-in above, that is the whole bar lock.
    incomingCue: barsKnown ? incoming.grid.downbeats[0] : incoming.grid.firstBeat,
    tail: Math.min(TAIL_BARS * secondsPerBar, Math.max(0, incomingUsable - blend)),
    barsKnown,
  };
}

/** Where the blend starts, expressed as an offset inside the outgoing clip.
 *
 * The invariant worth testing: when `barsKnown`, this is exactly one of the outgoing grid's
 * downbeats — not near one.
 */
export function blendPointInClip(alignment: Alignment): number {
  return alignment.outgoingStart + alignment.leadIn;
}
