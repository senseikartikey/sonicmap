"use client";

/*
  The Landing page's cinematic background: the same 3D brain-of-clusters visual built for the
  signed-in dashboard (TasteBrain.tsx), reused here decoratively for a signed-out visitor who
  has no map yet. Node positions come from decorativeBrain.ts's generated 140-point dataset —
  never real user data, and deliberately denser than any real account's brain so this never
  reads as "the same brain you're about to build," just the shape of what's possible. Camera
  motion is driven directly by scroll progress (a ref, not React state, so scrolling never
  triggers a re-render) rather than OrbitControls drag — the visitor never touches it,
  scrolling *is* the camera move.
*/

import { RefObject, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import * as THREE from "three";
import {
  AmbientParticles,
  Edges,
  Node,
  useBrainEdges,
  useBrainNodes,
} from "@/components/TasteBrain";
import { DECORATIVE_BRAIN_POINTS, isDecorativeHeroNode } from "@/components/decorativeBrain";

/** Reads scroll progress every render frame (not via React state/props) and turns it into a
 * continuous camera dolly-and-orbit: starts wide or the hero, ends close on the brain by the
 * closing section — plus a slow idle drift so the scene never looks frozen between scrolls. */
function ScrollCamera({
  scrollProgressRef,
  reduceMotion,
}: {
  scrollProgressRef: RefObject<number>;
  reduceMotion: boolean;
}) {
  const { camera } = useThree();
  const idleAngle = useRef(0);

  useFrame((_state, delta) => {
    const progress = scrollProgressRef.current ?? 0;
    if (!reduceMotion) idleAngle.current += delta * 0.035;
    const angle = idleAngle.current + progress * Math.PI * 1.6;
    const radius = 3.9 - progress * 1.7; // dolly inward across the whole page's scroll
    const height = 0.3 + Math.sin(progress * Math.PI) * 0.7;
    camera.position.set(Math.sin(angle) * radius, height, Math.cos(angle) * radius);
    camera.lookAt(0, 0, 0);
  });

  return null;
}

function Scene({
  nodes,
  edges,
  reduceMotion,
}: {
  nodes: ReturnType<typeof useBrainNodes>;
  edges: ReturnType<typeof useBrainEdges>;
  reduceMotion: boolean;
}) {
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_state, delta) => {
    if (!groupRef.current || reduceMotion) return;
    groupRef.current.rotation.y += delta * 0.025;
  });

  return (
    <group ref={groupRef}>
      <AmbientParticles nodes={nodes} />
      <Edges edges={edges} reduceMotion={reduceMotion} />
      {nodes.map((node) => (
        <Node
          key={node.point.song.id}
          node={node}
          isSelected={isDecorativeHeroNode(node.point.song.id)}
          isHovered={false}
          reduceMotion={reduceMotion}
          onSelect={() => {}}
          onHover={() => {}}
        />
      ))}
    </group>
  );
}

export function LandingBrain({ scrollProgressRef }: { scrollProgressRef: RefObject<number> }) {
  const nodes = useBrainNodes(DECORATIVE_BRAIN_POINTS);
  const edges = useBrainEdges(nodes);
  const reduceMotion =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  return (
    <Canvas
      camera={{ position: [0, 0.3, 3.9], fov: 50 }}
      dpr={[1, 1.8]}
      gl={{ alpha: true, antialias: true }}
      aria-hidden
    >
      <Scene nodes={nodes} edges={edges} reduceMotion={reduceMotion} />
      <ScrollCamera scrollProgressRef={scrollProgressRef} reduceMotion={reduceMotion} />
      <EffectComposer>
        <Bloom intensity={1.05} luminanceThreshold={0.22} luminanceSmoothing={0.4} mipmapBlur />
      </EffectComposer>
    </Canvas>
  );
}
