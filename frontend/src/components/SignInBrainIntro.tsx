"use client";

/*
  The transition between "Spotify just redirected back to us" and "the real dashboard is on
  screen": a cinematic fade-in and camera zoom into the same decorative brain used on the
  Landing background (decorativeBrain.ts) — GSAP-driven, the same tool named and used for the
  rest of the site's cinematic motion (Landing.tsx's scroll system), not a one-off library.

  Split into two phases so the transition is genuinely seamless rather than a fixed-length
  animation followed by a second, separate loading screen: the zoom-in always plays in full
  (consistent pacing, never feels rushed), then the scene *holds* — idle-rotating, not
  frozen — until the caller's `ready` prop goes true, and only then plays the fade-out and
  calls `onComplete`. The parent (page.tsx) mounts this as an overlay on top of the real
  dashboard while it loads underneath, and only unmounts it once that data is actually ready —
  so there is never a moment where this fades out onto anything but the finished page.

  Skips straight to onComplete under prefers-reduced-motion, same as every other motion
  sequence in the app.
*/

import { useEffect, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import gsap from "gsap";
import * as THREE from "three";
import {
  AmbientParticles,
  Edges,
  Node,
  useBrainEdges,
  useBrainNodes,
} from "@/components/TasteBrain";
import { DECORATIVE_BRAIN_POINTS, isDecorativeHeroNode } from "@/components/decorativeBrain";

/** Reads a 0→1 progress ref every frame (GSAP tweens the ref's `.current`, not React state)
 * and turns it into a single continuous dolly-in: starts wide, ends close and centered. Holds
 * exactly at the end position once progress reaches 1 — the holding group below is what keeps
 * things visibly alive during any wait past that point. */
function IntroCamera({ progressRef }: { progressRef: React.RefObject<number> }) {
  const { camera } = useThree();

  useFrame(() => {
    const p = progressRef.current;
    const radius = 9.5 - p * 6.3;
    const angle = p * Math.PI * 0.85;
    camera.position.set(Math.sin(angle) * radius, 0.5 - p * 0.2, Math.cos(angle) * radius);
    camera.lookAt(0, 0, 0);
  });

  return null;
}

function IntroScene() {
  const nodes = useBrainNodes(DECORATIVE_BRAIN_POINTS);
  const edges = useBrainEdges(nodes);
  const groupRef = useRef<THREE.Group>(null);

  // Independent slow idle rotation — keeps the scene visibly alive during an indefinite hold
  // (waiting on real data), not just during the fixed-length zoom.
  useFrame((_state, delta) => {
    if (groupRef.current) groupRef.current.rotation.y += delta * 0.04;
  });

  return (
    <group ref={groupRef}>
      <AmbientParticles nodes={nodes} />
      <Edges edges={edges} />
      {nodes.map((node) => (
        <Node
          key={node.point.song.id}
          node={node}
          isSelected={isDecorativeHeroNode(node.point.song.id)}
          isHovered={false}
          reduceMotion={false}
          onSelect={() => {}}
          onHover={() => {}}
        />
      ))}
    </group>
  );
}

export function SignInBrainIntro({ ready, onComplete }: { ready: boolean; onComplete: () => void }) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const progressRef = useRef(0);
  const [zoomDone, setZoomDone] = useState(false);
  const reducedMotionRef = useRef(false);

  // Phase 1: fade in + zoom, fixed duration, always plays in full regardless of `ready`.
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      reducedMotionRef.current = true;
      // Deferred (not called synchronously in the effect body) to avoid the cascading-render
      // lint warning — functionally identical, just fires on the next tick instead of inline.
      const handle = setTimeout(() => setZoomDone(true), 0);
      return () => clearTimeout(handle);
    }
    const tl = gsap.timeline({ onComplete: () => setZoomDone(true) });
    // overlayRef itself — the fixed, fullscreen backdrop — is opaque from the very first frame,
    // never animated. Whatever this overlay mounts on top of (the sign-in page's own plain
    // loading spinner, mid-navigation) must never be visible through it even for a moment; only
    // contentRef (the Canvas + copy) fades/scales in on top of that already-solid backdrop, so
    // the reveal reads as the scene materializing, not the backdrop itself blinking in.
    tl.set(contentRef.current, { opacity: 0, scale: 1.04 })
      .set(textRef.current, { opacity: 0, y: 10 })
      .to(contentRef.current, { opacity: 1, scale: 1, duration: 0.7, ease: "power2.out" })
      .to(textRef.current, { opacity: 1, y: 0, duration: 0.6, ease: "power2.out" }, "-=0.35")
      .to(progressRef, { current: 1, duration: 2.4, ease: "power2.inOut" }, "-=0.35");
    return () => {
      tl.kill();
    };
  }, []);

  // Phase 2: only once the zoom has finished *and* the real page is ready does the overlay
  // fade out — this is what makes the reveal land directly on the finished dashboard instead
  // of a second loading state.
  useEffect(() => {
    if (!zoomDone || !ready) return;
    if (reducedMotionRef.current) {
      onComplete();
      return;
    }
    const tl = gsap.timeline({ onComplete });
    tl.to(textRef.current, { opacity: 0, duration: 0.4, ease: "power1.in" }).to(
      overlayRef.current,
      { opacity: 0, duration: 0.5, ease: "power1.in" },
      "-=0.2"
    );
    return () => {
      tl.kill();
    };
  }, [zoomDone, ready, onComplete]);

  return (
    <div ref={overlayRef} className="fixed inset-0 z-[100] bg-[var(--bg-base)]" aria-hidden>
      <div ref={contentRef} className="absolute inset-0">
        <Canvas
          camera={{ position: [0, 0.5, 9.5], fov: 45 }}
          dpr={[1, 1.8]}
          gl={{ alpha: true, antialias: true }}
        >
          <IntroScene />
          <IntroCamera progressRef={progressRef} />
          <EffectComposer>
            <Bloom intensity={1.0} luminanceThreshold={0.2} luminanceSmoothing={0.4} mipmapBlur />
          </EffectComposer>
        </Canvas>
      </div>
      <div ref={textRef} className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3">
        <span className="font-mono text-[11px] uppercase tracking-[0.2em] text-[var(--accent)]">
          Welcome back
        </span>
        <p className="font-[family-name:var(--font-fraunces)] text-[26px] italic text-[var(--text-primary)]">
          Building your taste space…
        </p>
      </div>
    </div>
  );
}
