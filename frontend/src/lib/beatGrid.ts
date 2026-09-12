/** Finding where the beats actually fall inside a preview clip.
 *
 * The planner already knows each track's tempo — Essentia measured it during extraction — but
 * tempo alone cannot align two tracks. Knowing a clip is 120 BPM says a beat lands every 500 ms;
 * it says nothing about *which* 500 ms. Start two beat-matched tracks at an arbitrary moment and
 * every kick lands a fraction late against the other one, which is the flam that makes an
 * automated mix sound automated.
 *
 * So the only unknown is phase, and with the period already known that is a much smaller problem
 * than beat tracking: build an onset envelope, then slide a click train at the known period
 * across it and keep the offset where the most onset energy lines up. A comb filter, essentially.
 * It costs a few milliseconds and needs no model.
 */

export type BeatGrid = {
  /** Beats per minute the grid was built against — the planner's folded tempo, not the raw one. */
  bpm: number;
  secondsPerBeat: number;
  /** Seconds into the clip where the first beat lands. */
  firstBeat: number;
  /** Bar lines, when they are known. Empty for a grid estimated in the browser, which can find
   * a pulse but has no way to tell which beat is the "1". A player with no downbeats can only
   * beat-match; with them it can bar-match, which is the difference a listener actually hears. */
  downbeats: number[];
  /** Where the grid came from, for the UI to be honest about how exact the alignment is. */
  source: "server" | "estimated";
  /** How much the winning phase beat the average one. Above ~1.5 the grid is trustworthy;
   * near 1.0 the clip has no clear pulse (ambient, rubato, a fade-in) and the caller should
   * say so rather than pretend the alignment is exact. */
  confidence: number;
};

/* Measured, so nobody repeats the experiment: against Essentia's own RhythmExtractor2013 over
 * ten of the user's real tracks, this lands within 12% of a beat on 5 of 10 clips. Two obvious
 * refinements were tried and neither helped — weighting the onset envelope toward the kick with
 * a 200 Hz one-pole scored 5/10 (identical), and refining the tempo over a ±4% band scored 5/10
 * (it fixed three clips and broke two). Letting the comb filter choose the tempo octave was
 * actively worse: mean-score-per-click is biased toward slower periods, so it picked half time
 * on a 148 BPM track. The residual failures are swung and 6/8 material where the grid is
 * genuinely ambiguous rather than mis-detected — the same genre-heterogeneity that limits every
 * automatic DJ system in the literature. So this stays deliberately simple, reports `confidence`
 * honestly, and the player offers a manual nudge for the clips it gets wrong.
 */

/** 5 ms resolution. A beat at 120 BPM is 500 ms, so this puts phase error under 1% of a beat —
 * comfortably below what reads as a flam, while still cheap to compute on a 30-second clip. */
const FRAMES_PER_SECOND = 200;

/** Onset strength per frame: how much louder this frame is than the one before it.
 *
 * Half-wave rectified, so only *rises* count — a decaying note is not an onset. Amplitude is
 * used rather than raw energy because energy over-weights loud passages, which biases the comb
 * filter toward whichever section of the clip happens to be loudest rather than toward the pulse.
 */
function onsetEnvelope(buffer: AudioBuffer): Float32Array {
  const hop = Math.max(1, Math.round(buffer.sampleRate / FRAMES_PER_SECOND));
  const frames = Math.floor(buffer.length / hop);
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index));
  const amplitude = new Float32Array(frames);

  for (let frame = 0; frame < frames; frame += 1) {
    const start = frame * hop;
    let sum = 0;
    for (let offset = 0; offset < hop; offset += 1) {
      let mono = 0;
      for (const channel of channels) mono += channel[start + offset];
      sum += Math.abs(mono / channels.length);
    }
    amplitude[frame] = sum / hop;
  }

  const flux = new Float32Array(frames);
  for (let frame = 1; frame < frames; frame += 1) {
    flux[frame] = Math.max(0, amplitude[frame] - amplitude[frame - 1]);
  }
  return flux;
}

/** Slide a click train at the known beat period across the onset envelope and keep the offset
 * where the most onset energy lands on a click. */
export function detectBeatGrid(buffer: AudioBuffer, bpm: number): BeatGrid {
  const secondsPerBeat = 60 / bpm;
  const flux = onsetEnvelope(buffer);
  const period = secondsPerBeat * FRAMES_PER_SECOND;
  const fallback: BeatGrid = { bpm, secondsPerBeat, firstBeat: 0, confidence: 0, downbeats: [], source: "estimated" };
  if (!Number.isFinite(period) || period < 2 || flux.length < period * 2) return fallback;

  let best = -1;
  let bestPhase = 0;
  let total = 0;
  let tested = 0;
  const phases = Math.floor(period);

  for (let phase = 0; phase < phases; phase += 1) {
    let score = 0;
    let hits = 0;
    for (let position = phase; position < flux.length; position += period) {
      // Sum a tiny window rather than one frame: real drum hits are a few milliseconds wide and
      // sit slightly ahead of or behind the metronomic grid, so a single-frame probe misses them.
      const centre = Math.round(position);
      for (let offset = -1; offset <= 1; offset += 1) {
        const index = centre + offset;
        if (index >= 0 && index < flux.length) score += flux[index];
      }
      hits += 1;
    }
    if (hits === 0) continue;
    const mean = score / hits;
    total += mean;
    tested += 1;
    if (mean > best) {
      best = mean;
      bestPhase = phase;
    }
  }

  if (tested === 0 || best <= 0) return fallback;
  const average = total / tested;
  return {
    bpm,
    secondsPerBeat,
    firstBeat: bestPhase / FRAMES_PER_SECOND,
    confidence: average > 0 ? best / average : 0,
    downbeats: [],
    source: "estimated",
  };
}


/** Trusted meters, mirroring beat_grid.TRUSTED_METERS on the server: a 2-beat "bar" is almost
 * always the tracker reading a 4/4 song at half speed, and aligning on it would reintroduce the
 * half-bar error that downbeats exist to remove. */
const TRUSTED_METERS = [3, 4, 6];

/** Adapt a server-analysed grid, or return null if it is not solid enough to align bars to. */
export function fromServerGrid(
  grid: { bpm: number | null; beats: number[]; downbeats: number[]; beats_per_bar: number | null } | null | undefined,
  fallbackBpm: number,
): BeatGrid | null {
  if (!grid || !grid.downbeats?.length || (grid.beats?.length ?? 0) < 8) return null;
  if (!grid.beats_per_bar || !TRUSTED_METERS.includes(grid.beats_per_bar)) return null;
  const bpm = grid.bpm && grid.bpm > 0 ? grid.bpm : fallbackBpm;
  return {
    bpm,
    secondsPerBeat: 60 / bpm,
    firstBeat: grid.beats[0],
    downbeats: [...grid.downbeats],
    // A model that predicts beats and downbeats jointly is not in the same league as a comb
    // filter over an onset envelope, so this never needs the confidence caveat the estimate does.
    confidence: 99,
    source: "server",
  };
}


/** Seconds between bar lines, from the downbeats themselves rather than assumed from the tempo —
 * a track the tracker read in 3 has a different bar length to one it read in 4. */
export function secondsPerBar(grid: BeatGrid): number {
  if (grid.downbeats.length >= 2) {
    const gaps = grid.downbeats.slice(1).map((time, index) => time - grid.downbeats[index]).filter((gap) => gap > 0);
    if (gaps.length) {
      gaps.sort((a, b) => a - b);
      return gaps[Math.floor(gaps.length / 2)];
    }
  }
  return grid.secondsPerBeat * 4;
}


/** The first bar line at or after `seconds`. The incoming deck starts here so that its bar 1
 * lands on a bar 1 of the outgoing deck. */
export function downbeatAtOrAfter(grid: BeatGrid, seconds: number): number | null {
  return grid.downbeats.find((time) => time >= seconds - 1e-6) ?? null;
}


/** Bar lines that begin a phrase, i.e. every `phraseBars`-th bar. Starting a blend here means it
 * begins on the "1" of a section rather than merely on a bar line. */
export function phraseStarts(grid: BeatGrid, phraseBars = 4): number[] {
  return grid.downbeats.filter((_, index) => index % phraseBars === 0);
}


/** The latest phrase start at or before `limit` — where a blend can begin and still fit. */
export function lastPhraseStartBefore(grid: BeatGrid, limit: number, phraseBars = 4): number | null {
  const candidates = phraseStarts(grid, phraseBars).filter((time) => time <= limit);
  return candidates.length ? candidates[candidates.length - 1] : null;
}

/** The first beat at or after `seconds`, so playback can always start on the grid. */
export function beatAtOrAfter(grid: BeatGrid, seconds: number): number {
  if (seconds <= grid.firstBeat) return grid.firstBeat;
  const beats = Math.ceil((seconds - grid.firstBeat) / grid.secondsPerBeat);
  return grid.firstBeat + beats * grid.secondsPerBeat;
}

/** The last beat at or before `seconds`. Used to find where a clip can start so that a whole
 * number of bars still fits before it runs out. */
export function beatAtOrBefore(grid: BeatGrid, seconds: number): number {
  if (seconds <= grid.firstBeat) return grid.firstBeat;
  const beats = Math.floor((seconds - grid.firstBeat) / grid.secondsPerBeat);
  return grid.firstBeat + beats * grid.secondsPerBeat;
}
