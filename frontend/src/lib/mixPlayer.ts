/** A two-deck DJ mixer in the browser, so a planned transition can be heard rather than read.
 *
 * What this plays is the 30-second provider clip for each track — the only audio Sonicmap ever
 * has. That makes it an audition of the *technique*, not the finished mashup: a real 32-bar
 * blend runs longer than the clips themselves, so the blend is shortened to whatever whole
 * number of bars fits. Rendering a full mashup needs full audio, which only arrives through
 * Stem Studio's rights-confirmed upload path.
 *
 * Within that limit it is the real thing rather than a crossfade with a nice name. Each deck
 * runs through its own low/high shelf, tempo is matched by playback rate the way a pitch fader
 * does it, both decks are locked to a detected beat grid (see beatGrid.ts), and every technique
 * automates the shelves and faders differently — a bass swap really does hand the low end over
 * on a single downbeat.
 */
import { BeatGrid, detectBeatGrid, fromServerGrid, secondsPerBar as barSeconds } from "@/lib/beatGrid";
import { BEATS_PER_BAR, planAlignment } from "@/lib/mixAlignment";
import { PlannedTrack, PlannedTransition, TransitionKind } from "@/lib/types";
import { refreshPreviewUrl } from "@/lib/api";

export type LoadedTrack = { buffer: AudioBuffer; grid: BeatGrid };

/** How far the low end is pulled down when a technique takes it away. -26 dB is out of the way
 * without being a mute, which is what an EQ kill on a mixer actually sounds like. */
const BASS_CUT_DB = -26;
const SHELF_HZ = 220;

export type MixEvent =
  | { type: "loading" }
  | { type: "playing"; startedAt: number; duration: number; blendAt: number; blendBars: number; note: string }
  | { type: "ended" }
  | { type: "error"; message: string };

type Deck = {
  source: AudioBufferSourceNode;
  gain: GainNode;
  low: BiquadFilterNode;
  high: BiquadFilterNode;
};

/** Equal-power rather than linear: two linear ramps crossing at 0.5 sum to a dip in perceived
 * loudness right in the middle of the blend, which is exactly where a listener is judging it. */
function crossfadeCurve(rising: boolean, points = 64): Float32Array {
  const curve = new Float32Array(points);
  for (let index = 0; index < points; index += 1) {
    const position = index / (points - 1);
    curve[index] = rising ? Math.sin((position * Math.PI) / 2) : Math.cos((position * Math.PI) / 2);
  }
  return curve;
}

export class MixPlayer {
  private context: AudioContext | null = null;
  private cache = new Map<string, LoadedTrack>();
  private active: AudioBufferSourceNode[] = [];
  private stopTimer: number | null = null;

  /** Created lazily because a browser will not let an AudioContext start outside a user
   * gesture — the first play() call is that gesture. */
  private audio(): AudioContext {
    if (!this.context) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.context = new Ctor();
    }
    return this.context;
  }

  async load(track: PlannedTrack): Promise<LoadedTrack> {
    const cached = this.cache.get(track.id);
    if (cached) return cached;
    if (!track.bpm) throw new Error(`"${track.title}" has no measured tempo, so it can't be beat-matched.`);
    // A missing URL is not proof there is no clip — it may simply never have been resolved, or
    // the plan may predate the field. Ask for one before giving up.
    const stored = track.preview_url ?? (await refreshPreviewUrl(track.id));
    if (!stored) throw new Error(`No preview clip is available for "${track.title}".`);

    // Deezer serves previews from signed links that expire to a permanent 403 (about 9% of the
    // catalog is affected at any time), so a stored URL that fails is not necessarily a missing
    // track — it is usually a stale signature. Ask the backend for a fresh one and retry once.
    let bytes = await this.fetchClip(stored);
    if (!bytes) {
      const refreshed = await refreshPreviewUrl(track.id);
      bytes = refreshed ? await this.fetchClip(refreshed) : null;
    }
    if (!bytes) throw new Error(`The preview clip for "${track.title}" could not be loaded.`);
    const buffer = await this.audio().decodeAudioData(bytes);
    // Prefer the server's grid every time it exists. It knows where the bars are; the browser
    // estimate only ever finds a pulse, and a pulse without bar positions is what made blends
    // land on beat 3 as often as beat 1.
    const grid = fromServerGrid(track.beat_grid, track.bpm) ?? detectBeatGrid(buffer, track.bpm);
    const loaded = { buffer, grid };
    this.cache.set(track.id, loaded);
    return loaded;
  }

  private async fetchClip(url: string): Promise<ArrayBuffer | null> {
    try {
      const response = await fetch(url);
      return response.ok ? await response.arrayBuffer() : null;
    } catch {
      return null;
    }
  }

  stop() {
    for (const source of this.active) {
      try {
        source.stop();
      } catch {
        // Already stopped — this is the normal path when a preview ends on its own.
      }
    }
    this.active = [];
    if (this.stopTimer !== null) {
      window.clearTimeout(this.stopTimer);
      this.stopTimer = null;
    }
  }

  private deck(loaded: LoadedTrack, offset: number, rate: number): Deck {
    const context = this.audio();
    const source = context.createBufferSource();
    source.buffer = loaded.buffer;
    source.playbackRate.value = rate;

    const low = context.createBiquadFilter();
    low.type = "lowshelf";
    low.frequency.value = SHELF_HZ;
    const high = context.createBiquadFilter();
    high.type = "highshelf";
    high.frequency.value = 2400;
    const gain = context.createGain();

    source.connect(low).connect(high).connect(gain).connect(context.destination);
    this.active.push(source);
    void offset;
    return { source, gain, low, high };
  }

  /**
   * Audition one planned transition.
   *
   * The blend is shortened to whatever fits in two 30-second clips, because a 32-bar blend at
   * 100 BPM is 77 seconds of audio that does not exist here. The technique is not shortened —
   * the shelf and fader moves are the ones the plan calls for.
   */
  async playTransition(
    from: PlannedTrack,
    to: PlannedTrack,
    transition: PlannedTransition,
    onEvent: (event: MixEvent) => void,
    /** Manual alignment correction in beats, applied to the incoming deck. Beat detection on a
     * 30-second clip is right about half the time on heterogeneous material (see beatGrid.ts),
     * and no amount of tuning fixed the swung and 6/8 cases — so the listener gets the same
     * control a DJ has, and can pull the incoming track onto the grid by ear. */
    nudgeBeats = 0,
  ): Promise<void> {
    this.stop();
    onEvent({ type: "loading" });
    try {
      const [outgoing, incoming] = await Promise.all([this.load(from), this.load(to)]);
      const context = this.audio();
      if (context.state === "suspended") await context.resume();

      // Tempo match: the incoming deck is pulled onto the outgoing tempo, pitch and all, which
      // is exactly what a pitch fader does. For a `cut` the plan has already decided the two are
      // too far apart to hold together, so the incoming track plays at its own tempo.
      const rate = transition.kind === "cut" ? 1 : outgoing.grid.bpm / incoming.grid.bpm;
      const wantedBars = transition.kind === "cut" ? 1 : Math.max(2, Math.round(transition.bars / 4));

      // Where the blend actually happens. Pure and separately tested — see mixAlignment.ts.
      const {
        outgoingStart, leadIn, blend, blendBars, incomingCue, tail, barsKnown,
      } = planAlignment(
        { grid: outgoing.grid, duration: outgoing.buffer.duration },
        { grid: incoming.grid, duration: incoming.buffer.duration },
        { wantedBars, rate },
      );
      const secondsPerBar = outgoing.grid.downbeats.length >= 2
        ? barSeconds(outgoing.grid)
        : outgoing.grid.secondsPerBeat * BEATS_PER_BAR;
      const start = context.currentTime + 0.12;
      const blendAt = start + leadIn;
      const endAt = blendAt + blend + tail;

      // A negative nudge would seek before the clip starts, so the offset is clamped and the
      // remainder is applied to *when* deck B starts instead — same relative shift either way.
      const nudgeSeconds = nudgeBeats * incoming.grid.secondsPerBeat;
      const wantedOffset = incomingCue + nudgeSeconds;
      const incomingOffset = Math.max(0, wantedOffset);
      const startShift = (incomingOffset - wantedOffset) / rate;

      const deckA = this.deck(outgoing, outgoingStart, 1);
      const deckB = this.deck(incoming, incomingOffset, rate);

      deckA.gain.gain.setValueAtTime(1, start);
      deckB.gain.gain.setValueAtTime(0, start);
      deckA.low.gain.setValueAtTime(0, start);
      deckB.low.gain.setValueAtTime(0, start);
      deckA.high.gain.setValueAtTime(0, start);

      this.automate(transition.kind, deckA, deckB, blendAt, blend, secondsPerBar);

      deckA.source.start(start, outgoingStart);
      // Both decks are started from a detected beat, so they are phase-locked, not just
      // tempo-locked — the difference between a mix and two tracks playing at once.
      deckB.source.start(blendAt + startShift, incomingOffset);
      deckA.source.stop(blendAt + blend + 0.05);
      deckB.source.stop(endAt);

      const shortened = `${blendBars} bar${blendBars === 1 ? "" : "s"} of the planned ${transition.bars}, shortened to fit the 30-second clips.`;
      const confidence = Math.min(outgoing.grid.confidence, incoming.grid.confidence);
      const note = barsKnown
        ? `Bar-locked. ${shortened}`
        : confidence < 1.4
          ? "Beat grids are still being analysed for these tracks, so the alignment is a rough estimate — it will tighten once they finish."
          : `Beat-matched but not yet bar-locked (grid still analysing). ${shortened}`;

      onEvent({ type: "playing", startedAt: start, duration: endAt - start, blendAt: leadIn, blendBars, note });
      this.stopTimer = window.setTimeout(() => onEvent({ type: "ended" }), (endAt - context.currentTime) * 1000 + 60);
    } catch (reason) {
      onEvent({ type: "error", message: reason instanceof Error ? reason.message : String(reason) });
    }
  }

  /** The fader and shelf moves that make each technique sound like itself. */
  private automate(
    kind: TransitionKind,
    a: Deck,
    b: Deck,
    blendAt: number,
    blend: number,
    secondsPerBar: number,
  ) {
    const rising = crossfadeCurve(true);
    const falling = crossfadeCurve(false);

    if (kind === "cut") {
      // Nothing to hold together: the outgoing track simply stops on the downbeat.
      a.gain.gain.setValueAtTime(1, blendAt);
      a.gain.gain.linearRampToValueAtTime(0, blendAt + 0.02);
      b.gain.gain.setValueAtTime(1, blendAt);
      return;
    }

    if (kind === "bass_swap") {
      // The incoming track enters with its low end pulled out, so both can play without the
      // two basslines fighting; the low end changes hands on one beat in the middle.
      const swap = blendAt + blend / 2;
      b.low.gain.setValueAtTime(BASS_CUT_DB, blendAt);
      b.gain.gain.setValueCurveAtTime(rising, blendAt, blend / 2);
      b.low.gain.setValueAtTime(BASS_CUT_DB, swap - 0.03);
      b.low.gain.linearRampToValueAtTime(0, swap);
      a.low.gain.setValueAtTime(0, swap - 0.03);
      a.low.gain.linearRampToValueAtTime(BASS_CUT_DB, swap);
      a.gain.gain.setValueCurveAtTime(falling, swap, blend / 2);
      return;
    }

    if (kind === "long_blend") {
      // Nothing is fighting for attention, so this is a plain unhurried equal-power crossfade.
      b.gain.gain.setValueCurveAtTime(rising, blendAt, blend);
      a.gain.gain.setValueCurveAtTime(falling, blendAt, blend);
      return;
    }

    if (kind === "double_drop") {
      // Both tracks are at their peak: bring the second in fast and hold them together.
      b.gain.gain.setValueCurveAtTime(rising, blendAt, Math.min(blend, secondsPerBar));
      a.gain.gain.setValueAtTime(1, blendAt + blend - secondsPerBar);
      a.gain.gain.setValueCurveAtTime(falling, blendAt + blend - secondsPerBar, secondsPerBar);
      return;
    }

    if (kind === "rolling") {
      // Energy is lifting: the incoming track arrives underneath, then the outgoing one is
      // thinned from the bottom up so it evaporates instead of stopping.
      b.gain.gain.setValueCurveAtTime(rising, blendAt, blend * 0.6);
      a.low.gain.setValueAtTime(0, blendAt + blend * 0.4);
      a.low.gain.linearRampToValueAtTime(BASS_CUT_DB, blendAt + blend * 0.8);
      a.gain.gain.setValueCurveAtTime(falling, blendAt + blend * 0.5, blend * 0.5);
      return;
    }

    // echo_out: the keys clash, so the outgoing track is taken away quickly and darkly rather
    // than held against the incoming one.
    a.high.gain.setValueAtTime(0, blendAt);
    a.high.gain.linearRampToValueAtTime(-20, blendAt + blend * 0.5);
    a.gain.gain.setValueCurveAtTime(falling, blendAt, blend * 0.6);
    b.gain.gain.setValueCurveAtTime(rising, blendAt, blend * 0.4);
  }

  close() {
    this.stop();
    void this.context?.close();
    this.context = null;
    this.cache.clear();
  }
}
