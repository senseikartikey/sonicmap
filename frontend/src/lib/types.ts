export type CurrentUser = {
  id: string;
  spotify_id: string | null;
  display_name: string | null;
  email: string | null;
  auth_providers: string[];
  spotify_connected: boolean;
  is_guest: boolean;
};

export type Song = {
  id: string;
  title: string;
  artist: string;
  resolution_status: "pending" | "resolved" | "unresolved";
  extraction_status: "pending" | "extracted" | "failed";
  bpm: number | null;
  key: string | null;
  energy: number | null;
  danceability: number | null;
  /** From Essentia's Discogs-EffNet genre/style classifier — coarse top-level genre. */
  genre: string | null;
  /** Top few "Genre---Style" predictions, comma-joined (e.g. "Hip Hop---Trap, Hip Hop---Cloud Rap"). */
  styles: string | null;
  /** ISO 639-1 code detected from the title (e.g. "en", "ko"), or null if undetected —
   * a text-based proxy, not a guarantee of the actual vocal language. */
  language: string | null;
  /** 30s iTunes/Deezer preview clip — the same audio the extraction pipeline already
   * downloaded to compute this song's features. Null until extraction has resolved one. */
  preview_url: string | null;
};

export type MapPoint = {
  song: Song;
  x: number | null;
  y: number | null;
  cluster_label: number | null;
  added_at: string;
  source: "search" | "paste" | "spotify_playlist" | string;
};

export type Recommendation = {
  song: Song;
  distance: number;
  reason: string;
  best_match_song_id: string;
  xray: Record<string, number>;
  rank_change?: number;
};

export type CatalogStatus = {
  metadata: number;
  analyzed: number;
  queued: number;
  failed: number;
  version: number;
  enrichment_active: boolean;
};

export type StemJob = {
  id: string;
  source_type: "upload" | "youtube";
  source_name: string | null;
  model: "4stem" | "6stem";
  status: "queued" | "acquiring" | "transcoding" | "separating" | "packaging" | "ready" | "failed" | "cancelled" | "expired";
  stage: string;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  duration_seconds: number | null;
  analysis_ready: boolean;
  mapped_song_id: string | null;
  reused: boolean;
  created_at: string;
  expires_at: string | null;
};

export type ResonanceThread = { song_id: string; title: string; artist: string; match: number };

export type StemXRay = {
  overall_match: number | null;
  dominant: string | null;
  ready: boolean;
  stems: Array<{
    name: string;
    label: string;
    dimension: string;
    match: number | null;
    prominence: number;
    best_match: { id: string; title: string; artist: string } | null;
    /** Real cross-cluster matches on this one isolated stem layer — songs with nothing in
     * common genre/cluster-wise that still share this specific instrumental texture. See
     * TasteBrain.tsx's Resonance Threads rendering. */
    threads: ResonanceThread[];
    metrics: Record<string, number>;
  }>;
};

export type StemManifest = {
  job: StemJob;
  stems: Array<{ name: string; preview_url?: string; waveform_url?: string; download_url?: string }>;
  archive_url: string | null;
};

/** Derived from resolution_status / extraction_status / placement, since the API tracks
 * those as two independent stages rather than one combined state. */
export type PipelineState =
  | "resolving"
  | "unresolved"
  | "extracting"
  | "extraction_failed"
  | "unplaced"
  | "placed";

export function pipelineState(point: MapPoint): PipelineState {
  const { song } = point;
  if (song.resolution_status === "pending") return "resolving";
  if (song.resolution_status === "unresolved") return "unresolved";
  if (song.extraction_status === "pending") return "extracting";
  if (song.extraction_status === "failed") return "extraction_failed";
  if (point.x === null || point.y === null) return "unplaced";
  return "placed";
}

/** A status-line message with its severity, so the UI can style it without sniffing the
 * text for an "Error:" prefix — and so user-facing copy never has to spell that out. */
export type StatusMessage = { message: string; tone: "neutral" | "error" } | null;

export const PIPELINE_LABEL: Record<PipelineState, string> = {
  resolving: "Resolving",
  unresolved: "Unresolved",
  extracting: "Extracting",
  extraction_failed: "Extraction failed",
  unplaced: "Ready — needs rebuild",
  placed: "Placed",
};

// --- Set planner -------------------------------------------------------------------------
export type EnergyShape = "arc" | "build" | "peak" | "wind_down" | "wave";

/** The blend technique the planner chose. Each is a real DJ move, not a fade preset:
 * `bass_swap` crosses the low end on a downbeat, `double_drop` lands two peaks together,
 * `rolling` rides a tail into a drop, `echo_out` escapes a key clash, `cut` is the honest
 * answer when the tempos are too far apart to hold together. */
export type TransitionKind =
  | "bass_swap" | "long_blend" | "double_drop" | "rolling" | "echo_out" | "cut";

export type PlannedTrack = {
  id: string;
  title: string;
  artist: string;
  /** Octave-folded into a danceable range — `raw_bpm` is what extraction actually measured. */
  bpm: number | null;
  raw_bpm: number | null;
  key: string | null;
  camelot: string | null;
  energy: number | null;
  duration_ms: number | null;
  genre: string | null;
  /** The 30-second provider clip. Present for most catalog tracks; without it a transition
   * can be planned but not auditioned. */
  preview_url: string | null;
  /** Beat and downbeat times inside that clip, analysed server-side (see
   * backend/app/services/beat_grid.py). Null until the beat worker has reached this song; the
   * player then falls back to estimating beats in the browser, which finds no bars at all. */
  beat_grid: ServerBeatGrid | null;
};

export type ServerBeatGrid = {
  source: string;
  analyzed_at: string;
  bpm: number | null;
  beats: number[];
  /** Bar lines. These are the whole point — aligning two tracks on beats but not bars leaves
   * them a half-bar apart half the time, which is what makes an automated mix sound random. */
  downbeats: number[];
  /** Beats the analyser found between downbeats. 3, 4 or 6 means it located real bars; anything
   * else (or null) means the downbeats are noise and only the beats should be trusted. */
  beats_per_bar: number | null;
};

export type PlannedTransition = {
  from_id: string;
  to_id: string;
  kind: TransitionKind;
  bars: number;
  beats: number;
  seconds: number | null;
  tempo: { stretch_pct: number; ratio: "same" | "double" | "half"; within_comfort: boolean } | null;
  camelot_from: string | null;
  camelot_to: string | null;
  camelot_steps: number | null;
  score: { total: number; tempo: number; harmonic: number; sonic: number; energy: number };
  notes: string[];
  warnings: string[];
};

export type MashupPlan = {
  version: number;
  shape: EnergyShape;
  quality: number;
  /** Null unless every track in the set has a known length — the planner will not guess a
   * running time from partial data. */
  total_ms: number | null;
  tracks: PlannedTrack[];
  transitions: PlannedTransition[];
};

export type MashupSet = {
  id: string | null;
  name: string;
  shape: EnergyShape;
  source: "manual" | "recommendations" | "map";
  quality: number | null;
  plan: MashupPlan;
  created_at: string | null;
  updated_at: string | null;
};
