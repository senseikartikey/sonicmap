"use client";

/*
  THESIS: the taste map isn't a metaphor sonicmap gestures at — it's the literal product. The
  landing page should let a visitor watch it come alive before they've signed in, scrolling
  through it the way a film moves through scenes, not a stack of independent sections.
  OWN-WORLD: warm near-black, one surgical amber accent, Fraunces serif carrying hero/mechanism
  headlines with italic-amber accent words, JetBrains Mono for tracked micro-labels, one
  continuous fixed constellation-of-points background (never a per-section color swap) that
  scroll-zooms through the whole page, sections pinning briefly while their content resolves.
  STORY: a visitor scrolls through smoothly (Lenis-eased), watches the background continuously
  zoom through a connected map of points, reads the mechanism build word by word as its section
  holds the viewport, then signs in.
  FIRST VIEWPORT: centered wordmark top-left, drifting amber constellation filling the fixed
  background, serif headline with one italic-amber accent word, tracked micro-label above,
  glow CTA below, scroll cue at the foot.
  FORM: brief-pinned direction (skillsui.app/previews/Vanta-cinematic-scroll), technique named
  explicitly by the user's second reference (GSAP ScrollTrigger + Lenis — the actual stack
  named by github.com/MengTo/Skills' "cinematic-gsap-lenis-motion-system" /
  "gsap-scrolltrigger-storytelling" skill folders) — built with the real technique, not a
  static imitation of it. Code-led, no image generation this session.
  FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review,
  the verdict, DESIGN.md, and every shipping raster carrying its provenance.
*/

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { AudioWaveform, ScanSearch, Sparkles } from "lucide-react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";
import { apiFetch, setGuestSessionToken } from "@/lib/api";
import { SonicmapBrand } from "@/components/SonicmapBrand";

// Three.js/WebGL needs a real browser — same ssr:false pattern page.tsx uses for the
// dashboard's Brain view.
const LandingBrain = dynamic(() => import("@/components/LandingBrain").then((m) => m.LandingBrain), {
  ssr: false,
  loading: () => null,
});

const STEPS = [
  {
    n: "I",
    icon: ScanSearch,
    title: "Feed it songs",
    body: "Search, paste a list, or import a Spotify playlist — however your taste already lives.",
  },
  {
    n: "II",
    icon: AudioWaveform,
    title: "Real extraction",
    body: "Every song is resolved and run through Essentia for actual tempo, key, energy, and timbre — not a genre tag.",
  },
  {
    n: "III",
    icon: Sparkles,
    title: "Explore the shape",
    body: "Your songs land on a persistent map by how they sound. Distance is math, not a mystery algorithm.",
  },
];

/** The one background the whole page shares — fixed behind every section, never swapped for
 * a per-section color. The same 3D brain-of-clusters visual built for the signed-in dashboard
 * (LandingBrain.tsx, reusing TasteBrain.tsx's building blocks) carries the "camera moves
 * through the scene" motion, driven directly by scroll progress rather than drag/OrbitControls
 * — this is the actual product's own visual language, not a generic stock-footage stand-in. */
function CinematicBackground({ scrollProgressRef }: { scrollProgressRef: React.RefObject<number> }) {
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[var(--bg-base)]" aria-hidden>
      <LandingBrain scrollProgressRef={scrollProgressRef} />
      {/* Scrim: keeps headline/body text legible regardless of where the brain sits in frame,
          but stays light enough off-text that the brain — the whole point of this background —
          reads as a vivid, moving constellation rather than a dim silhouette under a dark
          sheet. Heaviest at the very top/bottom edges (wordmark, scroll cue, CTA footers),
          thinnest through the open middle band. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(180deg, rgba(10,8,6,0.46) 0%, rgba(10,8,6,0.16) 24%, rgba(10,8,6,0.18) 76%, rgba(10,8,6,0.52) 100%)",
        }}
      />
      {/* Centered vignette: every section here is vertically centered in the viewport
          (min-h-screen, flex-centered), so one fixed "spotlight in reverse" — darkest right
          behind the reading column, fading out fast toward the edges — guarantees contrast
          exactly where text sits without flattening the brain into a flat rectangle-behind-text
          look. Corners and edges stay fully dense/vivid, carrying the "wow" motion the linear
          scrim alone would wash out if pushed any darker on its own. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 52% 40% at 50% 50%, rgba(8,6,4,0.42) 0%, rgba(8,6,4,0.18) 46%, transparent 68%)",
        }}
      />
      <div
        className="absolute bottom-[-10%] left-1/2 h-[500px] w-[900px] -translate-x-1/2 rounded-full blur-[40px] [animation:orb-breathe_8s_ease-in-out_infinite_alternate]"
        style={{
          background:
            "radial-gradient(ellipse at center bottom, rgba(212,160,60,0.24) 0%, rgba(150,75,10,0.1) 35%, transparent 70%)",
        }}
      />
    </div>
  );
}

const PARTICLES = Array.from({ length: 18 }, (_, i) => ({
  id: i,
  left: (i * 37) % 100,
  delay: (i * 1.7) % 12,
  duration: 10 + (i % 6),
  driftX: ((i % 5) - 2) * 12,
}));

function Particles() {
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden>
      {PARTICLES.map((p) => (
        <span
          key={p.id}
          className="absolute bottom-0 h-[3px] w-[3px] rounded-full bg-[var(--accent)]"
          style={{
            left: `${p.left}%`,
            opacity: 0,
            ["--drift-x" as string]: `${p.driftX}px`,
            animation: `particle-drift ${p.duration}s ease-in ${p.delay}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

type Segment = { text: string; accent?: boolean };

const MECHANISM_SEGMENTS: Segment[] = [
  { text: "Every song becomes a set of real measurements — " },
  { text: "tempo", accent: true },
  { text: ", " },
  { text: "key", accent: true },
  { text: ", " },
  { text: "energy", accent: true },
  { text: ", " },
  { text: "timbre", accent: true },
  { text: " — pulled straight from the audio." },
];

const MECHANISM_SEGMENTS_2: Segment[] = [
  { text: "Not a " },
  { text: "genre tag", accent: true },
  { text: ". Not what " },
  { text: "someone else streamed", accent: true },
  { text: " next." },
];

function RevealLine({ segments }: { segments: Segment[] }) {
  return (
    <>
      {segments.map((seg, i) => (
        <span
          key={i}
          data-reveal-word
          className={
            seg.accent
              ? "font-[family-name:var(--font-fraunces)] italic text-[var(--accent-bright)]"
              : "text-[var(--text-primary)]"
          }
        >
          {seg.text}
        </span>
      ))}
    </>
  );
}

function AuthChoices({ spotifyUrl, googleUrl }: { spotifyUrl: string; googleUrl: string }) {
  const [note, setNote] = useState<string | null>(null);
  async function continueAsGuest() {
    try {
      const result = await apiFetch<{ token: string }>("/auth/guest", { method: "POST" });
      setGuestSessionToken(result.token);
      window.location.reload();
    } catch (reason) {
      setNote(reason instanceof Error ? reason.message : "Could not start guest mode.");
    }
  }
  const button = "group inline-flex min-h-12 items-center justify-center gap-3 rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[linear-gradient(180deg,rgba(255,248,230,0.055),rgba(255,248,230,0.018))] px-5 text-[12px] font-medium tracking-[0.01em] text-[var(--text-primary)] shadow-[inset_0_1px_0_rgba(255,255,255,0.045),0_10px_35px_rgba(0,0,0,0.2)] transition-[border-color,background,transform] duration-200 hover:-translate-y-px hover:border-[var(--border-accent)] hover:bg-[var(--accent-subtle)]";
  return (
    <div className="w-full max-w-md">
      <div className="grid gap-2 sm:grid-cols-2">
        <a href={googleUrl} className={button}><GoogleMark />Continue with Google</a>
        <a href={spotifyUrl} className={button}><SpotifyMark />Continue with Spotify</a>
      </div>
      <button onClick={() => void continueAsGuest()} className="mt-4 text-[12px] font-medium text-[var(--text-tertiary)] underline decoration-[var(--border-medium)] underline-offset-4 transition-colors hover:text-[var(--text-primary)] hover:decoration-[var(--accent)]">Continue as guest</button>
      {note && <p className="mt-2 text-[11px] text-[var(--error)]">{note}</p>}
    </div>
  );
}

function GoogleMark() {
  return <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden><path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.19-2.07H12v3.91h5.38a4.6 4.6 0 0 1-2 3.02v2.54h3.24c1.9-1.75 2.98-4.33 2.98-7.4Z"/><path fill="#34A853" d="M12 22c2.7 0 4.97-.9 6.62-2.37l-3.24-2.54c-.9.6-2.05.96-3.38.96-2.61 0-4.82-1.76-5.61-4.13H3.04v2.62A10 10 0 0 0 12 22Z"/><path fill="#FBBC05" d="M6.39 13.92A6.02 6.02 0 0 1 6.07 12c0-.67.11-1.32.32-1.92V7.46H3.04A10 10 0 0 0 2 12c0 1.61.38 3.14 1.04 4.54l3.35-2.62Z"/><path fill="#EA4335" d="M12 5.95c1.47 0 2.79.51 3.83 1.5l2.87-2.88A9.63 9.63 0 0 0 12 2a10 10 0 0 0-8.96 5.46l3.35 2.62C7.18 7.71 9.39 5.95 12 5.95Z"/></svg>;
}

function SpotifyMark() {
  return <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="10" fill="#1ED760"/><path d="M7.3 9.1c3.2-1 7.4-.74 10.18.76M7.86 12.15c2.73-.8 6.33-.6 8.72.63M8.4 15.02c2.27-.63 5.13-.48 7.13.5" fill="none" stroke="#0a0907" strokeWidth="1.45" strokeLinecap="round"/></svg>;
}

type CatalogPulse = { indexed: number; analyzed: number; processing: number; live: boolean };

function AnimatedCount({ value, decimals = 0, suffix = "" }: { value: number; decimals?: number; suffix?: string }) {
  const [shown, setShown] = useState(0);
  const previous = useRef(0);
  useEffect(() => {
    const start = previous.current;
    const began = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      const progress = Math.min(1, (now - began) / 1100);
      const eased = 1 - Math.pow(1 - progress, 4);
      setShown(start + (value - start) * eased);
      if (progress < 1) frame = requestAnimationFrame(tick);
      else previous.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <>{new Intl.NumberFormat("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(shown)}{suffix}</>;
}

function LiveCatalogProof() {
  const [pulse, setPulse] = useState<CatalogPulse | null>(null);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const next = await apiFetch<CatalogPulse>("/catalog/public-status");
        if (active) setPulse(next);
      } catch { /* The landing experience remains usable while the API wakes up. */ }
    };
    void refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const exactRankingReduction = pulse && pulse.analyzed > 0
    ? Math.max(0, (1 - Math.min(5_000, pulse.analyzed) / pulse.analyzed) * 100)
    : undefined;
  const metrics = [
    {
      value: 1_312,
      label: "signals per sonic fingerprint",
      detail: "32 rhythm, tonal and timbre descriptors joined with a 1,280-dimensional learned genre embedding.",
      decimals: 0,
      suffix: "",
    },
    {
      value: 5,
      label: "perceptual distances, one score",
      detail: "SonicDistance independently measures genre, timbre, rhythm, tonality and energy instead of hiding taste inside one opaque similarity.",
      decimals: 0,
      suffix: "",
    },
    {
      value: exactRankingReduction,
      label: "exact-ranking work avoided",
      detail: "The live ANN shortlist narrows today's mapped universe to at most 5,000 candidates, then SonicDistance performs the precise final ranking.",
      decimals: 1,
      suffix: "%",
    },
  ];
  return (
    <div className="w-full max-w-5xl">
      <div className="flex items-center justify-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${pulse?.live ? "bg-[var(--success)] shadow-[0_0_12px_var(--success)] animate-pulse" : "bg-[var(--text-muted)]"}`} />
        <p className="type-label text-[var(--accent)]">The SonicDistance advantage</p>
      </div>
      <p className="mx-auto mt-5 max-w-2xl font-[family-name:var(--font-fraunces)] text-[clamp(24px,3.4vw,40px)] font-medium leading-[1.25] tracking-[-0.02em] text-[var(--text-primary)]">
        More musical detail. Less ranking work. <span className="italic text-[var(--accent-bright)]">A distance built for taste.</span>
      </p>
      <div className="mt-10 grid grid-cols-1 border-y border-[var(--border-subtle)] sm:grid-cols-3 sm:divide-x sm:divide-[var(--border-subtle)]">
        {metrics.map((metric) => (
          <article key={metric.label} className="group relative overflow-hidden border-b border-[var(--border-subtle)] px-6 py-8 text-left last:border-b-0 sm:border-b-0">
            <div className="absolute inset-x-6 top-0 h-px origin-left scale-x-0 bg-gradient-to-r from-[var(--accent)] to-transparent transition-transform duration-700 group-hover:scale-x-100" />
            <p className="font-mono text-[clamp(30px,4vw,52px)] font-medium leading-none tracking-[-0.055em] text-[var(--accent-bright)] tabular-nums">
              {metric.value === undefined ? <span className="text-[var(--text-muted)]">—</span> : <AnimatedCount value={metric.value} decimals={metric.decimals} suffix={metric.suffix} />}
            </p>
            <p className="type-label mt-4 text-[var(--text-primary)]">{metric.label}</p>
            <p className="mt-3 text-[12px] leading-[1.6] text-[var(--text-tertiary)]">{metric.detail}</p>
          </article>
        ))}
      </div>
      <p className="mt-5 font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--text-muted)]">Efficiency recalculates live as the mapped universe grows · final matches remain exact SonicDistance scores</p>
    </div>
  );
}

export function Landing({ spotifyUrl, googleUrl }: { spotifyUrl: string; googleUrl: string }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const mechanismSectionRef = useRef<HTMLDivElement>(null);
  const mechanismTextRef = useRef<HTMLParagraphElement>(null);
  // A ref, not React state: the 3D brain's camera reads this every R3F render frame
  // (useFrame), so scroll must never trigger a React re-render of the whole tree.
  const scrollProgressRef = useRef(0);

  useEffect(() => {
    // Warms the post-sign-in cinematic transition's chunk while the visitor is still reading
    // this page — by far the most lead time it'll ever get before the Spotify OAuth round trip
    // lands back on "/" wanting it immediately. Fire-and-forget; a visitor who never signs in
    // just leaves a small cached chunk behind.
    void import("@/components/SignInBrainIntro");
  }, []);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return; // native scroll, no Lenis/ScrollTrigger, no pinning — brain camera stays static
    }

    gsap.registerPlugin(ScrollTrigger);

    const lenis = new Lenis({
      duration: 1.1,
      easing: (t: number) => 1 - Math.pow(1 - t, 3),
    });
    lenis.on("scroll", ScrollTrigger.update);
    gsap.ticker.add((time) => lenis.raf(time * 1000));
    gsap.ticker.lagSmoothing(0);

    const ctx = gsap.context(() => {
      // The one continuous scroll-drive for the whole page: the 3D brain's camera (see
      // LandingBrain.tsx's ScrollCamera) reads scrollProgressRef every frame and dollies/
      // orbits accordingly — this ScrollTrigger's only job is keeping that ref current.
      ScrollTrigger.create({
        trigger: rootRef.current,
        start: "top top",
        end: "bottom bottom",
        scrub: true,
        onUpdate: (self) => {
          scrollProgressRef.current = self.progress;
        },
      });

      // Mechanism section: pin for one viewport's worth of scroll while its sentence builds
      // word by word, scrubbed directly to scroll position — a real cinematic "chapter" hold,
      // not a tall div the reader has to scroll past.
      const words = mechanismTextRef.current?.querySelectorAll("[data-reveal-word]");
      if (words && words.length) {
        gsap.set(words, { opacity: 0.16 });
        gsap
          .timeline({
            scrollTrigger: {
              trigger: mechanismSectionRef.current,
              start: "top top",
              end: "+=100%",
              scrub: 0.4,
              pin: true,
              anticipatePin: 1,
            },
          })
          .to(words, { opacity: 1, stagger: { each: 1, from: "start" }, ease: "none" });
      }
    }, rootRef);

    return () => {
      ctx.revert();
      lenis.destroy();
    };
  }, []);

  return (
    <div ref={rootRef} className="relative">
      <CinematicBackground scrollProgressRef={scrollProgressRef} />
      <Particles />

      {/* ===== Hero ===== */}
      <section className="relative z-10 flex min-h-screen flex-col overflow-hidden">
        <div
          className="relative z-10 px-6 pt-8 text-[13px] font-medium tracking-[0.02em] text-[var(--text-body)] opacity-0 [animation:reveal-up_480ms_var(--ease-out)_forwards]"
          style={{ animationDelay: "0ms" }}
        >
          <SonicmapBrand />
        </div>

        <main className="relative z-10 flex flex-1 flex-col items-center justify-center px-6 pb-16 text-center">
          <div
            className="mb-7 inline-flex items-center gap-2 font-[family-name:var(--font-jetbrains-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--accent)] opacity-0 [animation:reveal-up_480ms_var(--ease-out)_forwards]"
            style={{ animationDelay: "80ms" }}
          >
            <span>Real audio analysis</span>
            <span className="text-[var(--text-muted)]">—</span>
            <span className="text-[var(--text-tertiary)]">not a black box</span>
          </div>

          <h1
            className="max-w-3xl font-[family-name:var(--font-fraunces)] text-[clamp(42px,7.8vw,96px)] font-semibold leading-[1.03] tracking-[-0.025em] text-[var(--text-primary)] opacity-0 [animation:reveal-up_480ms_var(--ease-out)_forwards]"
            style={{ animationDelay: "200ms" }}
          >
            Your taste,
            <br />
            mapped by how{" "}
            <span className="italic text-[var(--accent-bright)]">it</span>
            <br />
            <span className="italic text-[var(--accent-bright)]">sounds</span>.
          </h1>

          <p
            className="mt-7 max-w-xl text-[16px] leading-[1.65] text-[var(--text-body)] opacity-0 [animation:reveal-up_480ms_var(--ease-out)_forwards]"
            style={{ animationDelay: "320ms" }}
          >
            Sonicmap extracts real tempo, key, energy, and timbre from every song you feed it and
            places it on a persistent map — no collaborative filtering, no reset taste profile.
          </p>

          <div
            className="mt-10 opacity-0 [animation:reveal-up_480ms_var(--ease-out)_forwards]"
            style={{ animationDelay: "420ms" }}
          >
            <AuthChoices spotifyUrl={spotifyUrl} googleUrl={googleUrl} />
          </div>
        </main>

        <div
          className="relative z-10 flex flex-col items-center gap-2 pb-8 opacity-0 [animation:reveal-up_480ms_var(--ease-out)_forwards]"
          style={{ animationDelay: "520ms" }}
          aria-hidden
        >
          <span className="font-[family-name:var(--font-jetbrains-mono)] text-[11px] uppercase tracking-[0.2em] text-[var(--text-tertiary)]">
            Scroll to see how
          </span>
          <span className="h-8 w-px bg-gradient-to-b from-[var(--border-medium)] to-transparent" />
        </div>
      </section>

      {/* ===== Mechanism (pinned, scrubbed word-reveal) ===== */}
      <div ref={mechanismSectionRef} className="relative z-10 flex min-h-screen items-center justify-center px-6">
        <div className="mx-auto max-w-3xl text-center">
          <p className="mb-6 font-[family-name:var(--font-jetbrains-mono)] text-[11px] uppercase tracking-[0.18em] text-[var(--text-tertiary)]">
            The mechanism
          </p>
          <p
            ref={mechanismTextRef}
            className="font-[family-name:var(--font-fraunces)] text-[clamp(24px,4vw,42px)] font-medium leading-[1.3] tracking-[-0.01em]"
          >
            <RevealLine segments={MECHANISM_SEGMENTS} />
            <br className="hidden sm:block" />
            <RevealLine segments={MECHANISM_SEGMENTS_2} />
          </p>
        </div>
      </div>

      {/* ===== How it works ===== */}
      <section className="relative z-10 px-6 py-28">
        <h2 className="sr-only">How it works</h2>
        <div className="relative mx-auto grid max-w-[1100px] gap-14 sm:grid-cols-3">
          {/* Connecting line: reads the three steps as one continuous process rather than
              three independent tiles — the roman numerals carry real sequence, this line
              is what makes that sequence visible. */}
          <div
            className="absolute top-[15px] right-[16.5%] left-[16.5%] hidden h-px sm:block"
            style={{ background: "linear-gradient(90deg, var(--border-medium), var(--border-subtle) 50%, var(--border-medium))" }}
            aria-hidden
          />
          {STEPS.map((step) => (
            <div key={step.n} className="relative flex flex-col gap-4">
              <div className="flex items-center gap-3">
                <span className="relative z-10 bg-[var(--bg-base)] pr-1 font-[family-name:var(--font-fraunces)] text-[26px] italic leading-none text-[var(--accent-bright)]">
                  {step.n}
                </span>
                <step.icon size={17} strokeWidth={1.5} className="text-[var(--text-tertiary)]" />
              </div>
              <h3 className="font-[family-name:var(--font-fraunces)] text-[19px] font-medium tracking-[-0.01em] text-[var(--text-primary)]">
                {step.title}
              </h3>
              <p className="max-w-[30ch] text-[14px] leading-[1.65] text-[var(--text-body)]">{step.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ===== Closing proof ===== */}
      <section className="relative z-10 flex min-h-[70vh] flex-col items-center justify-center px-6 py-20 text-center">
        <LiveCatalogProof />
      </section>
    </div>
  );
}
