/*
  Shared synthetic dataset for every *decorative* brain rendering — the Landing page's
  cinematic background (LandingBrain.tsx) and the post-sign-in transition
  (SignInBrainIntro.tsx). Never a real user's map data (both surfaces run before any real
  account data is loaded), but deliberately much denser than a typical real account's brain
  (TasteBrain.tsx on the dashboard) so neither decorative surface reads as "the same brain you
  already have" — this is a showcase shape, hundreds of points, procedurally clustered rather
  than hand-placed.
*/

import { hash } from "@/components/TasteBrain";
import { MapPoint } from "@/lib/types";

const NODE_COUNT = 140;
const CLUSTER_COUNT = 8;

function makeSong(id: number) {
  return {
    id: `deco-${id}`,
    title: "",
    artist: "",
    resolution_status: "resolved" as const,
    extraction_status: "extracted" as const,
    bpm: null,
    key: null,
    energy: null,
    danceability: null,
    genre: null,
    styles: null,
    language: null,
    preview_url: null,
  };
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

/** Deterministic cluster centers spread around the map's 0-100 space, then each node jitters
 * around its assigned center — reads as genuine spatial clustering once projected onto the
 * brain surface, rather than uniform noise. */
function buildDecorativePoints(): MapPoint[] {
  const centers = Array.from({ length: CLUSTER_COUNT }, (_, i) => {
    const angle = (i / CLUSTER_COUNT) * Math.PI * 2;
    const spread = 26 + hash(`center-${i}`) * 10;
    return {
      x: 50 + Math.cos(angle) * spread,
      y: 50 + Math.sin(angle) * spread,
    };
  });

  return Array.from({ length: NODE_COUNT }, (_, i) => {
    const cluster = i % CLUSTER_COUNT;
    const center = centers[cluster];
    const jitterX = (hash(`n${i}x`) - 0.5) * 34;
    const jitterY = (hash(`n${i}y`) - 0.5) * 34;
    return {
      song: makeSong(i),
      x: clamp(center.x + jitterX, 2, 98),
      y: clamp(center.y + jitterY, 2, 98),
      cluster_label: cluster,
      added_at: "",
      source: "search",
    };
  });
}

export const DECORATIVE_BRAIN_POINTS: MapPoint[] = buildDecorativePoints();

/** A sparse, deterministic subset gets the bright amber "selected" glow (see Node.tsx) — same
 * technique as the old hand-placed constellation's "hero nodes," just picked by index instead
 * of by hand now that the dataset is generated. */
export function isDecorativeHeroNode(songId: string): boolean {
  const i = Number(songId.replace("deco-", ""));
  return Number.isFinite(i) && i % 23 === 0;
}
