"use client";

/*
  A third, opt-in spatial view alongside the existing 2D map and list table — never a
  replacement for either (see PRODUCT.md's accessibility commitment to a non-spatial way to
  reach every song, which the list view already satisfies and this view does not attempt to).
  Each node's position is derived from the *same* map_x/map_y the 2D map already places songs
  at (real UMAP/MDS taste-space coordinates), projected onto a procedurally-generated
  brain-shaped surface — this is the user's actual cluster structure taking a different shape,
  not a decorative random particle scatter.

  Cluster-formation animation: when a map rebuild changes where a song's node belongs (its
  position prop changes while the component stays mounted — i.e. the user is already looking
  at the brain when their real UMAP/HDBSCAN clustering finishes), that node doesn't snap to its
  new spot. It travels there over CLUSTER_FORM_TRAVEL_S, staggered per-node so the shape
  visibly reorganizes rather than jump-cutting — literally watching your own re-computed taste
  structure settle into place. While any node is traveling, the connections between newly-
  clustered neighbors (Edges, below) switch from their slow ambient shimmer to fast, bright,
  synapse-like traveling pulses — the same signal-firing motif that runs continuously, quietly,
  as this view's ambient resting state, so the brain never looks static even outside a rebuild.
  First mount (switching into this view) never triggers travel — nothing to travel from yet —
  so this only fires for a rebuild that completes while the user is already here.
*/

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html, Line, OrbitControls } from "@react-three/drei";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { Camera, Pause, Play, Video } from "lucide-react";
import * as THREE from "three";
import { MapPoint } from "@/lib/types";
import { EmptyState } from "@/components/EmptyState";

/** Same desaturated warm palette as TasteMap.tsx's CLUSTER_COLORS, so cluster identity reads
 * consistently whichever view a song is seen in. */
export const CLUSTER_COLORS = [
  "#4dd9ff", "#b693ff", "#ff6fae", "#ffc857", "#56e6a5", "#6f9dff", "#ff8b5f", "#7ce7e1",
];
const NOISE_COLOR = "#524c42";

// Cluster-formation timing (see module docstring). Travel is eased (ease-out cubic), so this is
// the ceiling, not a linear duration — most of a node's motion resolves well before it ends.
// FORMATION_SETTLE_MS outlives the longest possible per-node start delay + travel so the signal
// burst doesn't cut off while a late-starting node is still visibly moving.
export const CLUSTER_FORM_TRAVEL_S = 2.4;
const CLUSTER_FORM_MAX_DELAY_S = 0.5;
const FORMATION_SETTLE_MS = (CLUSTER_FORM_TRAVEL_S + CLUSTER_FORM_MAX_DELAY_S + 0.6) * 1000;

export function clusterColor(label: number | null): string {
  if (label === null || label < 0) return NOISE_COLOR;
  return CLUSTER_COLORS[label % CLUSTER_COLORS.length];
}

/** Cheap deterministic hash -> [0,1), used for stable per-song jitter (same song always lands
 * in the same spot across re-renders/sessions, no Math.random() drift). */
export function hash(str: string): number {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
  }
  return ((h >>> 0) % 10000) / 10000;
}

/** Procedural brain-shape radius at a given (theta, phi): an ellipsoid base, a shallow groove
 * at the vertical midline (the two-hemisphere fissure), and a handful of summed sine waves at
 * different frequencies standing in for cortical folds — a stylized "made of clusters, not a
 * medical scan" shape, deliberately evocative rather than anatomical. */
export function brainRadius(theta: number, phi: number): number {
  const base = 1.0;
  const fissure = 0.14 * Math.exp(-Math.pow(Math.sin(theta) * 2.2, 2)); // dips near theta=0/PI
  const folds =
    0.05 * Math.sin(theta * 6 + phi * 3) +
    0.035 * Math.sin(theta * 11 - phi * 5) +
    0.025 * Math.sin(theta * 17 + phi * 9);
  const taper = 0.9 + 0.1 * Math.sin(phi); // slightly narrower at top/bottom than the equator
  return (base - fissure + folds) * taper;
}

export type BrainNode = {
  point: MapPoint;
  position: [number, number, number];
  color: string;
};

/** One Resonance Thread target — a real cross-cluster match on a single isolated stem layer
 * (see backend/app/services/stem_intelligence.py's build_xray). `targetSongId` must be one of
 * the caller's own already-placed songs; a thread to a song not currently in `nodes` (removed
 * from the map since, say) is silently skipped by Scene rather than erroring. */
export type ResonanceThreadInfo = { targetSongId: string; label: string; match: number };

export function useBrainNodes(points: MapPoint[]): BrainNode[] {
  return useMemo(() => {
    const placed = points.filter((p) => p.x !== null && p.y !== null);
    if (placed.length === 0) return [];

    const xs = placed.map((p) => p.x as number);
    const ys = placed.map((p) => p.y as number);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;

    return placed.map((point) => {
      const nx = (((point.x as number) - minX) / spanX) * 2 - 1; // [-1, 1]
      const ny = (((point.y as number) - minY) / spanY) * 2 - 1; // [-1, 1]

      // nx selects hemisphere (left/right) and sweep within it; ny selects top-to-bottom band.
      const theta = nx * Math.PI;
      const phi = Math.PI / 2 + ny * Math.PI * 0.42;

      // How far this specific node sits from the base procedural shell is driven by its own
      // real energy/danceability, not just jitter — high-energy, danceable songs visibly push
      // outward past the shell; calm songs sit closer to the core. brainRadius() itself (and
      // the ambient particle cloud, which uses it directly) stays untouched as the "bone
      // structure," so the contrast between the fixed silhouette and where real songs actually
      // land is what makes this read as data, not re-randomized jitter. energy is already
      // normalized to [0,1] by extraction.py; Essentia's danceability isn't bounded the same
      // way (real values span roughly 0.75-5+), so it's rescaled against a practical ceiling
      // rather than assumed to already be a fraction.
      const energy = point.song.energy ?? 0.5;
      const danceability = Math.min((point.song.danceability ?? 1.5) / 3, 1);
      const dataPush = 0.85 + (energy * 0.6 + danceability * 0.4) * 0.3;

      const jitter = 0.94 + hash(point.song.id) * 0.09;
      const r = brainRadius(theta, phi) * jitter * dataPush * 1.6;

      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.cos(phi);
      const z = r * Math.sin(phi) * Math.sin(theta);

      return { point, position: [x, y, z] as [number, number, number], color: clusterColor(point.cluster_label) };
    });
  }, [points]);
}

/** Connects each node to its nearest same-cluster neighbor only — bounded to O(n) lines
 * regardless of catalog size, enough to read as a connected structure without a dense mesh. */
export function useBrainEdges(nodes: BrainNode[]): [THREE.Vector3, THREE.Vector3][] {
  return useMemo(() => {
    const edges: [THREE.Vector3, THREE.Vector3][] = [];
    const byCluster = new Map<number, BrainNode[]>();
    for (const node of nodes) {
      const label = node.point.cluster_label ?? -1;
      if (label < 0) continue;
      if (!byCluster.has(label)) byCluster.set(label, []);
      byCluster.get(label)!.push(node);
    }
    for (const members of byCluster.values()) {
      for (const a of members) {
        let nearest: BrainNode | null = null;
        let nearestDist = Infinity;
        for (const b of members) {
          if (a === b) continue;
          const d =
            (a.position[0] - b.position[0]) ** 2 +
            (a.position[1] - b.position[1]) ** 2 +
            (a.position[2] - b.position[2]) ** 2;
          if (d < nearestDist) {
            nearestDist = d;
            nearest = b;
          }
        }
        if (nearest) {
          edges.push([new THREE.Vector3(...a.position), new THREE.Vector3(...nearest.position)]);
        }
      }
    }
    return edges;
  }, [nodes]);
}

/** Small, dim, non-interactive points filling out the brain volume between the real song
 * nodes — pure texture/density, generated from the same brainRadius() shape function but at
 * random (theta, phi, depth) rather than any song's real position, and rendered clearly
 * smaller and dimmer than real nodes so they read as atmosphere, never as data. Each particle
 * is tinted toward its nearest real node's cluster color (a cheap O(particles × nodes) pass,
 * fine at this scale) rather than one flat neutral tone — the atmosphere itself now reads as
 * "which region of the brain is which cluster" at a glance, before a visitor even looks at
 * individual nodes. Existing count-driven "N PLACED" labels and the aria-label continue to
 * describe only the real nodes. */
function useAmbientParticles(nodes: BrainNode[]): { positions: Float32Array; colors: Float32Array } {
  return useMemo(() => {
    const count = Math.max(300, nodes.length * 8);
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const tmp = new THREE.Color();
    for (let i = 0; i < count; i++) {
      const theta = (hash(`p${i}a`) * 2 - 1) * Math.PI;
      const phi = Math.PI / 2 + (hash(`p${i}b`) * 2 - 1) * Math.PI * 0.48;
      const depth = 0.55 + hash(`p${i}c`) * 0.5; // scatter both near the surface and inside it
      const r = brainRadius(theta, phi) * depth * 1.6;
      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.cos(phi);
      const z = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3] = x;
      positions[i * 3 + 1] = y;
      positions[i * 3 + 2] = z;

      let nearestColor = "#8a7d5c";
      let nearestDist = Infinity;
      for (const node of nodes) {
        const dx = node.position[0] - x;
        const dy = node.position[1] - y;
        const dz = node.position[2] - z;
        const d = dx * dx + dy * dy + dz * dz;
        if (d < nearestDist) {
          nearestDist = d;
          nearestColor = node.color;
        }
      }
      tmp.set(nearestColor).multiplyScalar(0.75); // dimmed toward the base neutral, not full-saturation
      colors[i * 3] = tmp.r;
      colors[i * 3 + 1] = tmp.g;
      colors[i * 3 + 2] = tmp.b;
    }
    return { positions, colors };
  }, [nodes]);
}

export function AmbientParticles({ nodes }: { nodes: BrainNode[] }) {
  const { positions, colors } = useAmbientParticles(nodes);
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    return g;
  }, [positions, colors]);
  return (
    <points geometry={geometry}>
      <pointsMaterial
        size={0.013}
        vertexColors
        sizeAttenuation
        transparent
        opacity={0.5}
        depthWrite={false}
        toneMapped={false}
      />
    </points>
  );
}

// Ambient vs. formation-burst pace/brightness for the traveling signal points — see Edges.
// Ambient is deliberately slow and dim: a resting "still alive" quality, not something
// competing for attention. Burst is the authored moment.
const SIGNAL_AMBIENT_SPEED = 0.05; // laps per second along an edge
const SIGNAL_BURST_SPEED = 0.6;
const SIGNAL_AMBIENT_OPACITY = 0.32;
const SIGNAL_BURST_OPACITY = 0.95;
const EDGE_CURVE_SAMPLES = 10;

/** Deterministic (edges never re-seed on re-render) gentle bow, offset roughly tangent to the
 * brain's own surface at the segment's midpoint — real dendrites don't run ruler-straight
 * between cell bodies, and a dead-straight line between two spheres is what reads as "generic
 * node-link diagram" rather than "neural structure." A quadratic bezier is enough curvature to
 * sell it without the cost of a full spline. */
export function useEdgeCurves(edges: [THREE.Vector3, THREE.Vector3][]): THREE.QuadraticBezierCurve3[] {
  return useMemo(
    () =>
      edges.map(([a, b], i) => {
        const mid = new THREE.Vector3().addVectors(a, b).multiplyScalar(0.5);
        const span = new THREE.Vector3().subVectors(b, a);
        const radial = mid.clone().normalize();
        let bowAxis = new THREE.Vector3().crossVectors(span, radial);
        if (bowAxis.lengthSq() < 1e-6) bowAxis = new THREE.Vector3(1, 0, 0);
        bowAxis.normalize();
        const bow = (hash(`edge-bow-${i}-${edges.length}`) - 0.5) * span.length() * 0.7;
        mid.addScaledVector(bowAxis, bow);
        return new THREE.QuadraticBezierCurve3(a, mid, b);
      }),
    [edges]
  );
}

/** The full connective structure: every curved edge's fat, glowing line drawn in ONE combined
 * draw call (a single LineSegments2 fed every edge's sampled sub-segments back to back via
 * drei's `segments` mode), rather than one Line component per edge — a per-edge version was a
 * direct cause of a real, reported slowdown, since each drei Line carries its own LineMaterial/
 * resolution tracking. Plain WebGL lineBasicMaterial is capped at 1px on most platforms
 * regardless of its linewidth prop, which is why going back to that isn't an option — this
 * keeps the fat/visible line while paying for it once, not per edge. Traveling signals
 * (synapse pulses) stay one small mesh per edge — those genuinely need independent per-edge
 * position state — with no separate glow-halo mesh; bloom already supplies the glow, a second
 * mesh per edge was cost without a look anyone could tell apart from bloom alone.
 * `formationActive` swaps every signal from a slow ambient shimmer to a fast, bright,
 * synchronized-feeling burst for the cluster-formation moment. Exported name kept as `Edges` for
 * LandingBrain.tsx/SignInBrainIntro.tsx's existing `<Edges edges={edges} .../>` call sites. */
export function Edges({
  edges,
  formationActive = false,
  reduceMotion = false,
}: {
  edges: [THREE.Vector3, THREE.Vector3][];
  formationActive?: boolean;
  reduceMotion?: boolean;
}) {
  const curves = useEdgeCurves(edges);
  const linePoints = useMemo(() => {
    const pts: THREE.Vector3[] = [];
    for (const curve of curves) {
      const sampled = curve.getPoints(EDGE_CURVE_SAMPLES);
      for (let i = 0; i < sampled.length - 1; i++) pts.push(sampled[i], sampled[i + 1]);
    }
    return pts;
  }, [curves]);

  // Stable per-edge phase/speed jitter (hash-derived, not Math.random()) so the same edge set
  // doesn't visibly re-seed on every re-render, and so pulses don't all fire in obvious unison
  // even before a formation burst intentionally makes them feel synchronized.
  const phases = useMemo(() => edges.map((_, i) => hash(`edge-phase-${i}-${edges.length}`)), [edges]);
  const speedJitter = useMemo(() => edges.map((_, i) => 0.75 + hash(`edge-speed-${i}-${edges.length}`) * 0.5), [edges]);
  const signalRefs = useRef<(THREE.Mesh | null)[]>([]);
  const tmp = useMemo(() => new THREE.Vector3(), []);

  useFrame((state) => {
    if (reduceMotion) return;
    const speed = formationActive ? SIGNAL_BURST_SPEED : SIGNAL_AMBIENT_SPEED;
    const peakOpacity = formationActive ? SIGNAL_BURST_OPACITY : SIGNAL_AMBIENT_OPACITY;
    curves.forEach((curve, i) => {
      const mesh = signalRefs.current[i];
      if (!mesh) return;
      const t = (state.clock.elapsedTime * speed * speedJitter[i] + phases[i]) % 1;
      curve.getPointAt(t, tmp);
      mesh.position.copy(tmp);
      // Fades in/out near each endpoint (sin(t*PI) peaks at the segment midpoint) so it reads
      // as a signal traveling between two neurons, not a dot popping in and teleporting out.
      (mesh.material as THREE.MeshBasicMaterial).opacity = Math.sin(t * Math.PI) * peakOpacity;
    });
  });

  return (
    <>
      {linePoints.length > 0 && (
        <Line points={linePoints} segments color="#d4a03c" lineWidth={1.2} transparent opacity={0.2} toneMapped={false} depthWrite={false} />
      )}
      {!reduceMotion &&
        curves.map((_, i) => (
          <mesh
            key={i}
            ref={(el) => {
              signalRefs.current[i] = el;
            }}
          >
            <sphereGeometry args={[0.017, 8, 8]} />
            <meshBasicMaterial color="#f0c869" transparent opacity={0} toneMapped={false} depthWrite={false} />
          </mesh>
        ))}
    </>
  );
}

// A dedicated hue for Resonance Threads — deliberately not amber (the cluster-edge/selection
// color) so a thread never reads as "just another cluster connection." Violet against the
// warm palette everywhere else in this view is the one place the brain breaks its own color
// language on purpose, because what it's showing is a genuinely different *kind* of connection
// (one isolated instrumental layer, not the whole-track proximity every other line represents).
const RESONANCE_COLOR = "#c68aff";
const RESONANCE_SPEED = 0.16; // laps per second — steadier and a touch faster than Edges'
// ambient shimmer; this is meant to draw the eye, not blend into the resting texture.

/** Renders the (small, bounded — a handful of threads at most) set of cross-cluster
 * Resonance Thread connections from a Stem Studio session's source song to its real matches on
 * one isolated layer. Deliberately its own component, not folded into Edges: these need a
 * different color, pace, and lifetime (persistent until the caller clears them, not tied to
 * the cluster-formation ambient/burst cycle), even though the underlying curved-line technique
 * is the same one Edges already established works well at this scale. Same batched-single-
 * draw-call discipline as Edges — see that component's docstring for why a version of this
 * that instantiated one drei Line per thread was a real, reported performance regression. */
function ResonanceThreads({
  edges,
  reduceMotion = false,
}: {
  edges: [THREE.Vector3, THREE.Vector3][];
  reduceMotion?: boolean;
}) {
  const curves = useEdgeCurves(edges);
  const linePoints = useMemo(() => {
    const pts: THREE.Vector3[] = [];
    for (const curve of curves) {
      const sampled = curve.getPoints(EDGE_CURVE_SAMPLES);
      for (let i = 0; i < sampled.length - 1; i++) pts.push(sampled[i], sampled[i + 1]);
    }
    return pts;
  }, [curves]);
  const phases = useMemo(() => edges.map((_, i) => hash(`resonance-phase-${i}-${edges.length}`)), [edges]);
  const signalRefs = useRef<(THREE.Mesh | null)[]>([]);
  const tmp = useMemo(() => new THREE.Vector3(), []);

  useFrame((state) => {
    if (reduceMotion) return;
    curves.forEach((curve, i) => {
      const mesh = signalRefs.current[i];
      if (!mesh) return;
      const t = (state.clock.elapsedTime * RESONANCE_SPEED + phases[i]) % 1;
      curve.getPointAt(t, tmp);
      mesh.position.copy(tmp);
      (mesh.material as THREE.MeshBasicMaterial).opacity = Math.sin(t * Math.PI) * 0.85;
    });
  });

  if (edges.length === 0) return null;

  return (
    <>
      <Line points={linePoints} segments color={RESONANCE_COLOR} lineWidth={1.6} transparent opacity={0.5} toneMapped={false} depthWrite={false} />
      {!reduceMotion &&
        curves.map((_, i) => (
          <mesh
            key={i}
            ref={(el) => {
              signalRefs.current[i] = el;
            }}
          >
            <sphereGeometry args={[0.022, 8, 8]} />
            <meshBasicMaterial color={RESONANCE_COLOR} transparent opacity={0} toneMapped={false} depthWrite={false} />
          </mesh>
        ))}
    </>
  );
}

export function Node({
  node,
  isSelected,
  isHovered,
  locatorActive = false,
  isLocatorTarget = false,
  isResonanceSource = false,
  isResonanceTarget = false,
  reduceMotion,
  onSelect,
  onHover,
  playingId,
  dataArrayRef,
  onTogglePreview,
}: {
  node: BrainNode;
  isSelected: boolean;
  isHovered: boolean;
  locatorActive?: boolean;
  isLocatorTarget?: boolean;
  /** The song a Resonance Threads exploration started from, vs. one of its real cross-cluster
   * matches — see ResonanceThreads above. Deliberately independent of locatorActive: unlike the
   * quick 3-second locator pulse, this is meant to stay on screen while the visitor actually
   * looks at and rotates around the connections, so it never dims the rest of the brain (that
   * would hide the very cluster context that makes a cross-cluster thread read as surprising). */
  isResonanceSource?: boolean;
  isResonanceTarget?: boolean;
  reduceMotion: boolean;
  onSelect: () => void;
  onHover: (hovering: boolean) => void;
  /** Audio-reactive playback (optional — LandingBrain.tsx's decorative reuse omits all four
   * and gets the plain selected-node pulse below). Exactly one node plays at a time, so the
   * analyser/data buffer live once in TasteBrain and are threaded down rather than each node
   * owning its own AudioContext. */
  playingId?: string | null;
  dataArrayRef?: RefObject<Uint8Array<ArrayBuffer> | null>;
  onTogglePreview?: (songId: string, previewUrl: string) => void;
}) {
  const meshRef = useRef<THREE.Mesh>(null);
  const haloRef = useRef<THREE.Mesh>(null);
  const isPlaying = playingId === node.point.song.id;

  // Cluster-formation travel (see module docstring). Position is driven entirely imperatively
  // via groupRef once mounted — no `position` prop on the outer <group> below — because a
  // declarative position prop would just re-snap to node.position on every re-render and there
  // would be nothing left to animate. fromRef/toRef start equal to the node's first position,
  // so first mount never travels (nothing to travel *from* yet); a later change to
  // node.position (a real rebuild while this node stays mounted) sets a new "from" (wherever
  // the node actually is right now) and "to" (the new target) and starts the ease.
  const groupRef = useRef<THREE.Group>(null);
  const fromRef = useRef(new THREE.Vector3(...node.position));
  const toRef = useRef(new THREE.Vector3(...node.position));
  const travelStartRef = useRef<number | null>(null);
  const travelingRef = useRef(false);
  const mountedRef = useRef(false);
  // Deterministic per-node start offset (not Math.random()) so a rebuild's travel reads as an
  // organic, staggered settle rather than every node moving in mechanical lockstep — same
  // stable-hash technique the file already uses for jitter.
  const startDelay = useMemo(() => hash(`${node.point.song.id}-formdelay`) * CLUSTER_FORM_MAX_DELAY_S, [node.point.song.id]);
  // Per-node phase for the idle breathing animation below — same stable-hash technique, so the
  // same node always breathes on the same offset rather than resetting on every re-render.
  const idlePhase = useMemo(() => hash(`${node.point.song.id}-breathe`) * Math.PI * 2, [node.point.song.id]);
  const baseOpacity = locatorActive
    ? isLocatorTarget ? 1 : 0.13
    : node.point.cluster_label === null || node.point.cluster_label < 0 ? 0.55 : 0.92;
  // Resonance-highlighted nodes never dim below near-full opacity, even a noise-cluster song —
  // the thread itself is the point, not this node's usual cluster-membership opacity rule.
  const restingOpacity = !locatorActive && (isResonanceSource || isResonanceTarget) ? Math.max(baseOpacity, 0.95) : baseOpacity;

  const [px, py, pz] = node.position;
  useLayoutEffect(() => {
    const next = new THREE.Vector3(px, py, pz);
    if (!mountedRef.current) {
      fromRef.current.copy(next);
      toRef.current.copy(next);
      groupRef.current?.position.copy(next);
      mountedRef.current = true;
      return;
    }
    if (toRef.current.equals(next)) return;
    fromRef.current.copy(groupRef.current?.position ?? next);
    toRef.current.copy(next);
    travelStartRef.current = null;
    travelingRef.current = true;
  }, [px, py, pz]);

  useFrame((state) => {
    if (groupRef.current && travelingRef.current) {
      if (reduceMotion) {
        groupRef.current.position.copy(toRef.current);
        travelingRef.current = false;
        groupRef.current.updateMatrixWorld();
      } else {
        if (travelStartRef.current === null) travelStartRef.current = state.clock.elapsedTime;
        const elapsed = state.clock.elapsedTime - travelStartRef.current - startDelay;
        if (elapsed >= 0) {
          const t = Math.min(elapsed / CLUSTER_FORM_TRAVEL_S, 1);
          const eased = 1 - Math.pow(1 - t, 3); // ease-out cubic — a confident arrival, no bounce
          groupRef.current.position.lerpVectors(fromRef.current, toRef.current, eased);
          if (t >= 1) travelingRef.current = false;
          // Forces this group's world matrix current *this* frame rather than leaving it to
          // whenever Three's own render traversal would otherwise get to it — drei's <Html>
          // below projects off the group's world matrix to place the tooltip, and without this
          // there's a real risk of it reading a stale (last-frame) transform while the node is
          // actively traveling/rotating, which reads as the tooltip "sticking" instead of
          // tracking the node.
          groupRef.current.updateMatrixWorld();
        }
      }
    }

    if (!meshRef.current || reduceMotion) return;
    if (locatorActive) {
      if (haloRef.current) {
        haloRef.current.scale.setScalar(1);
        (haloRef.current.material as THREE.MeshBasicMaterial).opacity = isLocatorTarget ? 0.16 : 0.02;
      }
      if (isLocatorTarget) {
        const blink = 0.5 + 0.5 * Math.sin(state.clock.elapsedTime * 12);
        meshRef.current.scale.setScalar(1.05 + blink * 0.55);
        (meshRef.current.material as THREE.MeshBasicMaterial).opacity = 0.5 + blink * 0.5;
      } else {
        meshRef.current.scale.setScalar(0.88);
      }
      return;
    }
    if (playingId && dataArrayRef?.current?.length) {
      // A shared sampler updates the FFT buffer once per frame. Each node listens to a
      // deterministic band, making bass, mids and highs ripple through different regions.
      const bins = dataArrayRef.current;
      const center = Math.floor((idlePhase / (Math.PI * 2)) * bins.length) % bins.length;
      const left = bins[(center + bins.length - 1) % bins.length];
      const amplitude = (left + bins[center] * 2 + bins[(center + 1) % bins.length]) / (4 * 255);
      // Compress the FFT range upward: preview clips commonly peak well below 1.0, so a raw
      // mapping looked almost static. The exponent preserves musical variation while making
      // quiet passages visibly reactive too.
      const reactive = Math.min(1, Math.pow(amplitude, 0.52) * 1.45);
      meshRef.current.scale.setScalar(0.82 + reactive * 0.9);
      (meshRef.current.material as THREE.MeshBasicMaterial).opacity = 0.18 + reactive * 0.82;
      if (haloRef.current) {
        haloRef.current.scale.setScalar(0.92 + reactive * 0.85);
        (haloRef.current.material as THREE.MeshBasicMaterial).opacity = 0.04 + reactive * 0.32;
      }
      return;
    }
    (meshRef.current.material as THREE.MeshBasicMaterial).opacity = restingOpacity;
    if (haloRef.current) {
      haloRef.current.scale.setScalar(1);
      (haloRef.current.material as THREE.MeshBasicMaterial).opacity = restingOpacity * 0.16;
    }
    if (isSelected) {
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 2.6) * 0.18;
      meshRef.current.scale.setScalar(pulse);
      return;
    }
    if (isResonanceSource || isResonanceTarget) {
      // A slightly stronger, slower pulse than the shared idle breathe — reads as "this node
      // is part of something," distinct from every other node's own quiet idle motion.
      const pulse = 1 + Math.sin(state.clock.elapsedTime * (isResonanceSource ? 1.6 : 1.1) + idlePhase) * 0.12;
      meshRef.current.scale.setScalar(pulse);
      return;
    }
    // Idle breathing on every resting node, not just the selected one — per-node phase (hash-
    // derived, not synchronized) so hundreds of nodes never move in visible lockstep. This is
    // what keeps the structure reading as alive when nothing else is happening, rather than a
    // frozen point cloud that only moves when clicked.
    const breathe = 1 + Math.sin(state.clock.elapsedTime * 0.85 + idlePhase) * 0.07;
    meshRef.current.scale.setScalar(breathe);
  });

  const radius = isSelected ? 0.045 : isHovered ? 0.038 : isResonanceSource ? 0.04 : 0.026;
  const color = locatorActive
    ? isLocatorTarget ? "#ffd76a" : "#77736b"
    : isSelected || isHovered
    ? "#e8b84e"
    : isResonanceSource || isResonanceTarget
    ? RESONANCE_COLOR
    : node.color;
  const opacity = restingOpacity;
  const song = node.point.song;

  return (
    <group ref={groupRef}>
      {/* Guarded on song.title so a decorative reuse of this component with synthetic,
          title-less points (LandingBrain.tsx) never renders an empty tooltip chip. */}
      {(isSelected || isHovered) && song.title && (
        <Html distanceFactor={4.5} position={[0, radius + 0.07, 0]} center style={{ pointerEvents: "none" }}>
          <div className="w-max max-w-[152px] rounded-[var(--r-sm)] border border-[var(--border-medium)] bg-[var(--bg-card)] px-2 py-1 text-[9px] leading-snug shadow-[0_4px_16px_rgba(0,0,0,0.5)]">
            <div className="flex min-w-0 items-center gap-1">
              <p className="min-w-0 truncate font-medium text-[var(--text-primary)]">{song.title}</p>
              {song.preview_url && onTogglePreview && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onTogglePreview(song.id, song.preview_url as string);
                  }}
                  aria-label={isPlaying ? `Pause preview of ${song.title}` : `Play preview of ${song.title}`}
                  className="pointer-events-auto ml-auto flex shrink-0 items-center justify-center text-[var(--accent)] hover:text-[var(--text-primary)]"
                >
                  {isPlaying ? (
                    <Pause size={9} strokeWidth={1.5} fill="currentColor" />
                  ) : (
                    <Play size={9} strokeWidth={1.5} fill="currentColor" />
                  )}
                </button>
              )}
            </div>
            <p className="truncate text-[var(--text-body)]">{song.artist}</p>
            {(song.bpm !== null || song.genre) && (
              <p className="mt-0.5 truncate font-mono text-[8px] text-[var(--text-tertiary)]">
                {song.bpm !== null ? `${Math.round(song.bpm)} BPM` : ""}
                {song.bpm !== null && song.genre ? " · " : ""}
                {song.genre ?? ""}
              </p>
            )}
          </div>
        </Html>
      )}
      <mesh ref={meshRef}>
        <sphereGeometry args={[radius, 20, 20]} />
        {/* toneMapped=false + a color pushed past 1.0 is what actually blooms — a plain 0-255
            color reads as flat even with EffectComposer's Bloom pass attached upstream. */}
        <meshBasicMaterial
          color={color}
          transparent
          opacity={opacity}
          toneMapped={false}
        />
      </mesh>
      {/* A slightly larger, very transparent halo mesh behind the core sphere — cheap way to
          thicken the glow beyond what Bloom alone gives a single small sphere. */}
      <mesh ref={haloRef}>
        <sphereGeometry args={[radius * 1.9, 12, 12]} />
        <meshBasicMaterial color={color} transparent opacity={opacity * 0.16} toneMapped={false} depthWrite={false} />
      </mesh>
      {/* Generous invisible hit target, same rationale as TasteMap's — the visible node stays
          small and precise, the clickable area doesn't. */}
      <mesh
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        onPointerOver={(e) => {
          e.stopPropagation();
          onHover(true);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          onHover(false);
          document.body.style.cursor = "auto";
        }}
      >
        <sphereGeometry args={[0.09, 8, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  );
}

/** A single expanding, fading ring at a node's position — visualizes score_candidates'
 * real cluster-consensus scoring as an event ("here's the cluster this recommendation
 * actually matched against"), rather than an abstract backend computation. Rendered inside
 * the same rotating group as the nodes it targets, in the same local (pre-rotation)
 * coordinates, so it tracks correctly as the brain rotates. Self-terminating: calls
 * onComplete exactly once when its animation finishes, guarded against useFrame firing that
 * more than once after the duration is reached. */
function LocatorTimer({ onComplete }: { onComplete: () => void }) {
  useEffect(() => {
    const timeout = setTimeout(onComplete, 3000);
    return () => clearTimeout(timeout);
  }, [onComplete]);
  return null;
}

/** Samples the shared analyser once per frame; every node reads the same fresh FFT buffer. */
function AudioSampler({
  active,
  analyserRef,
  dataArrayRef,
}: {
  active: boolean;
  analyserRef?: RefObject<AnalyserNode | null>;
  dataArrayRef?: RefObject<Uint8Array<ArrayBuffer> | null>;
}) {
  useFrame(() => {
    if (active && analyserRef?.current && dataArrayRef?.current) {
      analyserRef.current.getByteFrequencyData(dataArrayRef.current);
    }
  });
  return null;
}

/** True for FORMATION_SETTLE_MS after `nodes`' positions genuinely change while this stays
 * mounted (a rebuild landed while the user is already looking at the brain) — never on first
 * mount, since there's nothing to compare against yet. Drives Edges from its ambient shimmer
 * into the bright, fast formation burst; the per-node travel animation itself (see Node) runs
 * independently off the same nodes-changed signal, so the two stay in sync without this hook
 * needing to control them directly. */
function useFormationActive(nodes: BrainNode[], reduceMotion: boolean): boolean {
  const [active, setActive] = useState(false);
  const prevKeyRef = useRef<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const key = nodes.map((n) => `${n.point.song.id}:${n.position.join(",")}`).join("|");
    const prev = prevKeyRef.current;
    prevKeyRef.current = key;
    if (prev === null || prev === key || reduceMotion) return;
    setActive(true);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setActive(false), FORMATION_SETTLE_MS);
  }, [nodes, reduceMotion]);

  useEffect(
    () => () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    },
    []
  );

  return active;
}

function Scene({
  nodes,
  edges,
  selectedId,
  onSelect,
  reduceMotion,
  pulseFromSongId,
  onPulseComplete,
  playingId,
  analyserRef,
  dataArrayRef,
  onTogglePreview,
  resonanceSourceId,
  resonanceThreads,
}: {
  nodes: BrainNode[];
  edges: [THREE.Vector3, THREE.Vector3][];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  reduceMotion: boolean;
  pulseFromSongId?: string | null;
  onPulseComplete?: () => void;
  playingId?: string | null;
  analyserRef?: RefObject<AnalyserNode | null>;
  dataArrayRef?: RefObject<Uint8Array<ArrayBuffer> | null>;
  onTogglePreview?: (songId: string, previewUrl: string) => void;
  resonanceSourceId?: string | null;
  resonanceThreads?: ResonanceThreadInfo[];
}) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const groupRef = useRef<THREE.Group>(null);
  const formationActive = useFormationActive(nodes, reduceMotion);

  useFrame((_state, delta) => {
    if (!groupRef.current || reduceMotion) return;
    groupRef.current.rotation.y += delta * 0.08;
  });

  const locatorActive = Boolean(pulseFromSongId && nodes.some((n) => n.point.song.id === pulseFromSongId));

  // Resonance Thread lines: source-node position -> each real cross-cluster match's position,
  // computed fresh from the current node layout so threads still track correctly through a
  // cluster-formation travel animation. Threads to a song that isn't currently placed (removed
  // from the map since the thread was fetched) are silently dropped, not an error state.
  const resonanceTargetIds = useMemo(
    () => new Set((resonanceThreads ?? []).map((t) => t.targetSongId)),
    [resonanceThreads]
  );
  const resonanceLines = useMemo(() => {
    if (!resonanceSourceId || !resonanceThreads?.length) return [];
    const source = nodes.find((n) => n.point.song.id === resonanceSourceId);
    if (!source) return [];
    const lines: [THREE.Vector3, THREE.Vector3][] = [];
    for (const thread of resonanceThreads) {
      const target = nodes.find((n) => n.point.song.id === thread.targetSongId);
      if (target) lines.push([new THREE.Vector3(...source.position), new THREE.Vector3(...target.position)]);
    }
    return lines;
  }, [nodes, resonanceSourceId, resonanceThreads]);

  return (
    <group ref={groupRef}>
      <AudioSampler
        active={Boolean(playingId)}
        analyserRef={analyserRef}
        dataArrayRef={dataArrayRef}
      />
      <AmbientParticles nodes={nodes} />
      <Edges edges={edges} formationActive={formationActive} reduceMotion={reduceMotion} />
      <ResonanceThreads edges={resonanceLines} reduceMotion={reduceMotion} />
      {nodes.map((node) => (
        <Node
          key={node.point.song.id}
          node={node}
          isSelected={selectedId === node.point.song.id}
          isHovered={hoveredId === node.point.song.id}
          locatorActive={locatorActive}
          isLocatorTarget={pulseFromSongId === node.point.song.id}
          isResonanceSource={resonanceSourceId === node.point.song.id}
          isResonanceTarget={resonanceTargetIds.has(node.point.song.id)}
          reduceMotion={reduceMotion}
          onSelect={() => onSelect(selectedId === node.point.song.id ? null : node.point.song.id)}
          onHover={(hovering) => setHoveredId(hovering ? node.point.song.id : null)}
          playingId={playingId}
          dataArrayRef={dataArrayRef}
          onTogglePreview={onTogglePreview}
        />
      ))}
      {locatorActive && onPulseComplete && (
        <LocatorTimer key={pulseFromSongId} onComplete={onPulseComplete} />
      )}
    </group>
  );
}

type ClusterLegendEntry = {
  label: number;
  color: string;
  count: number;
  genre: string | null;
  name: string;
};

/** Turns the same cluster colors already on every node into a legend: which color is which
 * cluster, how many songs, and (best-effort) what genre dominates it — the "informative" half
 * of the view, so the shape isn't just pretty, it's readable. */
function useClusterLegend(nodes: BrainNode[]): ClusterLegendEntry[] {
  return useMemo(() => {
    const byCluster = new Map<number, { color: string; count: number; genres: Map<string, number> }>();
    for (const node of nodes) {
      const label = node.point.cluster_label;
      if (label === null || label < 0) continue;
      if (!byCluster.has(label)) {
        byCluster.set(label, { color: node.color, count: 0, genres: new Map() });
      }
      const entry = byCluster.get(label)!;
      entry.count += 1;
      const genre = node.point.song.genre;
      if (genre) entry.genres.set(genre, (entry.genres.get(genre) ?? 0) + 1);
    }
    return Array.from(byCluster.entries())
      .map(([label, { color, count, genres }]) => {
        const rankedGenres = [...genres.entries()].sort((a, b) => b[1] - a[1]);
        const topGenre = rankedGenres[0]?.[0] ?? null;
        const genreBlend = rankedGenres.slice(0, 2).map(([genre]) => genre).join(" / ");
        const name = genreBlend || `Cluster ${label + 1}`;
        return { label, color, count, genre: topGenre, name };
      })
      .sort((a, b) => b.count - a.count);
  }, [nodes]);
}

export function TasteBrain({
  points,
  selectedId,
  onSelect,
  pulseFromSongId,
  onPulseComplete,
  resonanceSourceId,
  resonanceThreads,
  resonanceLabel,
  onClearResonance,
}: {
  points: MapPoint[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Song id of one of the user's *own* already-placed songs to pulse from — e.g. the
   * best_match_song_id a recommendation matched against — not a new/unplaced song. */
  pulseFromSongId?: string | null;
  onPulseComplete?: () => void;
  /** Resonance Threads (see ResonanceThreads/stem_intelligence.py's build_xray) — a Stem
   * Studio session's source song plus its real cross-cluster matches on one isolated layer.
   * Both must be set together for anything to render; persists until the caller clears it via
   * onClearResonance (there's no auto-timeout — unlike the locator pulse above, this is meant
   * to stay on screen while the visitor actually explores it). */
  resonanceSourceId?: string | null;
  resonanceThreads?: ResonanceThreadInfo[];
  /** Which stem layer these threads came from ("Vocal character", "Rhythmic instinct", …).
   * Without it the violet lines are unattributed — the visitor arrives from Stem Studio having
   * asked about one specific layer, and the overlay is the only place that answer survives. */
  resonanceLabel?: string;
  onClearResonance?: () => void;
}) {
  const nodes = useBrainNodes(points);
  const edges = useBrainEdges(nodes);
  const legend = useClusterLegend(nodes);
  const reduceMotion =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Audio-reactive playback: one shared <audio>/AudioContext/AnalyserNode for the whole view,
  // not one per node — createMediaElementSource can only be attached to a given <audio>
  // element once ever, and only one preview plays at a time anyway. Node reads analyserRef/
  // dataArrayRef directly inside its own useFrame when it's the one playing (see Node above).
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioElRef = useRef<HTMLAudioElement | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const dataArrayRef = useRef<Uint8Array<ArrayBuffer> | null>(null);

  const ensureAudioGraph = useCallback(() => {
    if (audioElRef.current) return;
    const audio = new Audio();
    audio.crossOrigin = "anonymous";
    audio.addEventListener("ended", () => setPlayingId(null));
    const ctx = new AudioContext();
    const source = ctx.createMediaElementSource(audio);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    audioElRef.current = audio;
    audioCtxRef.current = ctx;
    analyserRef.current = analyser;
    dataArrayRef.current = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
  }, []);

  const handleTogglePreview = useCallback(
    (songId: string, previewUrl: string) => {
      ensureAudioGraph();
      const audio = audioElRef.current;
      if (!audio) return;
      if (playingId === songId) {
        audio.pause();
        setPlayingId(null);
        return;
      }
      void audioCtxRef.current?.resume();
      if (audio.src !== previewUrl) audio.src = previewUrl;
      audio.currentTime = 0;
      void audio.play().catch(() => setPlayingId(null));
      setPlayingId(songId);
    },
    [ensureAudioGraph, playingId]
  );

  // Preview audio must not keep playing after the user leaves this view (switches to map/list,
  // or the whole page unmounts) — pause and tear down the AudioContext, don't just orphan it.
  useEffect(() => {
    return () => {
      audioElRef.current?.pause();
      void audioCtxRef.current?.close();
    };
  }, []);

  // Set via <Canvas>'s onCreated below — the raw WebGL canvas element, needed to read real
  // pixels out (toDataURL/captureStream). gl={{ preserveDrawingBuffer: true }} on the Canvas
  // is what makes toDataURL return the actual rendered frame instead of a blank one; by
  // default WebGL clears the drawing buffer right after each frame presents.
  const canvasElRef = useRef<HTMLCanvasElement | null>(null);

  const handleSaveImage = useCallback(() => {
    const source = canvasElRef.current;
    if (!source) return;
    const out = document.createElement("canvas");
    out.width = source.width;
    out.height = source.height;
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(source, 0, 0);

    const topGenre = legend[0]?.genre;
    const statsLine = `${nodes.length} songs · ${legend.length} clusters${topGenre ? ` · ${topGenre}` : ""}`;
    const pad = 22;
    ctx.font = "600 24px system-ui, sans-serif";
    const textWidth = ctx.measureText(statsLine).width;
    ctx.fillStyle = "rgba(12, 10, 8, 0.68)";
    ctx.fillRect(0, out.height - 72, Math.max(textWidth, 220) + pad * 2, 72);
    ctx.fillStyle = "#e8b84e";
    ctx.font = "600 15px system-ui, sans-serif";
    ctx.fillText("SONICMAP", pad, out.height - 42);
    ctx.fillStyle = "#f5f0e6";
    ctx.font = "600 24px system-ui, sans-serif";
    ctx.fillText(statsLine, pad, out.height - 16);

    const link = document.createElement("a");
    link.download = "sonicmap-brain.png";
    link.href = out.toDataURL("image/png");
    link.click();
  }, [legend, nodes.length]);

  const handleSaveClip = useCallback(() => {
    const source = canvasElRef.current as (HTMLCanvasElement & { captureStream?: (fps?: number) => MediaStream }) | null;
    if (!source?.captureStream || typeof MediaRecorder === "undefined") return;
    const stream = source.captureStream(30);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
    const chunks: BlobPart[] = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: "video/webm" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.download = "sonicmap-brain.webm";
      link.href = url;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    };
    recorder.start();
    setTimeout(() => recorder.stop(), 4000); // a few seconds of the existing ambient auto-rotate
  }, []);

  if (nodes.length === 0) {
    return (
      <EmptyState
        title="Nothing to shape yet"
        body="Once a few songs are placed on your map, they take the shape of a brain here too — same taste-space positions, one more way to see them."
      />
    );
  }

  return (
    <div
      className="relative h-full min-h-[420px] w-full overflow-hidden rounded-[var(--r-lg)] bg-[var(--bg-media)]"
      role="img"
      aria-label={`3D brain-shaped view of your taste map — ${nodes.length} songs as connected, rotatable points. The same songs are also available in the map and list views.`}
    >
      <div className="pointer-events-none absolute left-4 top-4 z-10 font-mono text-[11px] tracking-[0.04em] text-[var(--text-tertiary)] tabular">
        [{nodes.length} PLACED] // [{legend.length} CLUSTERS] // [DRAG TO ROTATE]
      </div>
      {resonanceSourceId && resonanceThreads && resonanceThreads.length > 0 && (
        <div className="pointer-events-auto absolute left-4 top-12 z-10 flex items-center gap-2 rounded-full border border-[#c68aff]/30 bg-[var(--bg-card)]/85 py-1.5 pl-3 pr-1.5 backdrop-blur-sm">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#c68aff] shadow-[0_0_6px_#c68aff]" aria-hidden />
          <span className="font-mono text-[10px] tracking-[0.04em] text-[var(--text-body)]">
            {resonanceLabel ? `${resonanceLabel} · ` : ""}
            {resonanceThreads.length} thread{resonanceThreads.length === 1 ? "" : "s"}
          </span>
          {/* The threads are otherwise purely a WebGL drawing inside a role="img" container,
              i.e. invisible to anyone not reading the canvas. This is the same commitment the
              list view exists for: the actual finding — which songs matched, and how closely —
              stated in text. */}
          <span className="sr-only">
            {`Cross-cluster matches on this layer: ${resonanceThreads
              .map((thread) => `${thread.label}, ${thread.match}% match`)
              .join("; ")}.`}
          </span>
          {onClearResonance && (
            <button
              onClick={onClearResonance}
              aria-label="Clear resonance threads"
              className="flex h-5 w-5 items-center justify-center rounded-full text-[var(--text-tertiary)] transition-colors hover:bg-white/[0.08] hover:text-[var(--text-primary)]"
            >
              ×
            </button>
          )}
        </div>
      )}
      {legend.length > 0 && (
        <div
          className="pointer-events-auto absolute right-4 top-4 z-10 flex max-w-48 flex-wrap items-center justify-end gap-2 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-card)]/75 px-3 py-2 backdrop-blur-sm"
          aria-label="Taste clusters"
        >
          {legend.map((c) => (
            <span
              key={c.label}
              className="group relative flex h-3 w-3 items-center justify-center"
            >
              <span
                tabIndex={0}
                className="h-2.5 w-2.5 cursor-help rounded-full ring-1 ring-white/15 transition-transform hover:scale-125 focus:scale-125 focus:outline-none"
                style={{ background: c.color }}
                aria-label={`${c.name}, ${c.count} songs`}
              />
              <span
                role="tooltip"
                className="pointer-events-none absolute right-0 top-[calc(100%+9px)] z-20 w-max max-w-48 translate-y-1 rounded-[var(--r-sm)] border border-[var(--border-medium)] bg-[var(--bg-card)] px-2.5 py-2 text-left opacity-0 shadow-[0_8px_24px_rgba(0,0,0,0.55)] backdrop-blur-md transition-[opacity,transform] duration-150 group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:opacity-100"
              >
                <span className="block text-[10px] font-medium leading-[1.35] text-[var(--text-primary)]">{c.name}</span>
                <span className="mt-0.5 block font-mono text-[9px] text-[var(--text-tertiary)]">{c.count} songs</span>
              </span>
            </span>
          ))}
        </div>
      )}
      <div className="absolute bottom-4 right-4 z-10 flex gap-1 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-card)] p-1">
        <button
          onClick={handleSaveImage}
          className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-body)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)]"
          aria-label="Save image of your brain"
        >
          <Camera size={14} strokeWidth={1.5} />
        </button>
        <button
          onClick={handleSaveClip}
          className="flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] text-[var(--text-body)] transition-colors hover:bg-white/[0.05] hover:text-[var(--text-primary)]"
          aria-label="Save short clip of your brain"
        >
          <Video size={14} strokeWidth={1.5} />
        </button>
      </div>
      <Canvas
        camera={{ position: [0, 0, 3.4], fov: 45 }}
        dpr={[1, 2]}
        gl={{ preserveDrawingBuffer: true }}
        onCreated={(state) => {
          canvasElRef.current = state.gl.domElement;
        }}
      >
        <Scene
          nodes={nodes}
          edges={edges}
          selectedId={selectedId}
          onSelect={onSelect}
          reduceMotion={reduceMotion}
          pulseFromSongId={pulseFromSongId}
          onPulseComplete={onPulseComplete}
          playingId={playingId}
          analyserRef={analyserRef}
          dataArrayRef={dataArrayRef}
          onTogglePreview={handleTogglePreview}
          resonanceSourceId={resonanceSourceId}
          resonanceThreads={resonanceThreads}
        />
        {/* Real bloom post-processing (EffectComposer + Bloom), not a fake glow drawn by hand —
            this is what actually gives the halo meshes and bright accent nodes a soft luminous
            spread instead of a hard-edged circle. Modest intensity/threshold: it should lift
            the already-bright selected/hovered nodes and the amber connective lines, not wash
            out the whole scene. */}
        <EffectComposer>
          <Bloom
            intensity={0.9}
            luminanceThreshold={0.25}
            luminanceSmoothing={0.4}
            mipmapBlur
          />
        </EffectComposer>
        <OrbitControls
          enablePan={false}
          minDistance={1.8}
          maxDistance={6}
          autoRotate={false}
          enableDamping
          dampingFactor={0.08}
        />
      </Canvas>
    </div>
  );
}
