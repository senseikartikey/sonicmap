---
name: sonicmap
description: An instrument for reading your own taste as a shape — warm near-black surfaces, one surgical amber accent, real extracted audio metadata set in monospace.
colors:
  warm-black-base: "#0a0907"
  warm-black-surface: "#100f0d"
  warm-black-card: "#161412"
  warm-black-panel: "#141210"
  warm-black-media: "#0e0c0a"
  ivory-primary: "#f0ebe0"
  ivory-body: "#a89e8c"
  ivory-muted: "#6a6357"
  ivory-tertiary: "#6b6355"
  amber-accent: "#d4a03c"
  amber-bright: "#e8b84e"
  border-subtle: "rgba(255, 248, 230, 0.07)"
  border-medium: "rgba(255, 248, 230, 0.13)"
  border-accent: "rgba(212, 160, 60, 0.55)"
  accent-glow: "rgba(212, 160, 60, 0.18)"
  accent-subtle: "rgba(212, 160, 60, 0.08)"
  signal-success: "#4fb571"
  signal-error: "#d1554f"
  signal-info: "#6a9fd4"
typography:
  display:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "clamp(40px, 7vw, 88px)"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.03em"
  title:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "normal"
  label:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0.04em"
  caption:
    fontFamily: "Inter, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  sm: "6px"
  md: "10px"
  lg: "16px"
  xl: "20px"
  pill: "999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "20px"
  6: "24px"
  8: "32px"
  10: "40px"
  12: "48px"
  16: "64px"
components:
  button-primary:
    backgroundColor: "{colors.warm-black-card}"
    textColor: "{colors.ivory-primary}"
    rounded: "{rounded.md}"
    padding: "12px 24px"
  button-secondary:
    backgroundColor: "{colors.warm-black-card}"
    textColor: "{colors.ivory-primary}"
    rounded: "{rounded.md}"
    padding: "12px 24px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ivory-body}"
    rounded: "{rounded.md}"
    padding: "12px 24px"
  card:
    backgroundColor: "{colors.warm-black-card}"
    rounded: "{rounded.lg}"
    padding: "24px"
  input:
    backgroundColor: "{colors.warm-black-surface}"
    textColor: "{colors.ivory-primary}"
    rounded: "{rounded.md}"
    padding: "10px 12px"
---

# Design System: sonicmap

## Overview

**Creative North Star: "The Instrument Panel"**

Sonicmap is built as an instrument, not a dashboard or a Wrapped-style recap: the taste map is the product, and every panel around it exists to feed or read that map. The visual world is a warm near-black room lit by a single amber signal — one surgical accent reserved for primary action, selection, and live state, never spent on decoration or on distinguishing one cluster from another. Technical data (BPM, key, energy, danceability) is treated as real design material, set in monospace as literal `//`-separated metadata lines rather than as invented flavor copy — the system's signature adaptation of a dark-luxury reference world's marketing-grammar trope into something the app actually measures.

The world is pinned from an external dark-luxury reference and translated from that reference's Persuade (marketing/landing) register into this app's Operate (signed-in, task-dense) register. The signed-out Landing page is the one place the Persuade register is allowed to run fully — full-bleed hero, breathing orb, staged reveal animation. Every signed-in surface is denser, flatter in rhythm, and organized around the three-column Operate grid (ingest + pipeline / map or table / selection + recommendations).

**Key Characteristics:**
- Warm near-black base (#0a0907, never pure black) with a fixed film-grain overlay across the whole app
- One amber accent (#d4a03c) spent only on primary actions, selection, and live/attention state
- Cards raised by an inset highlight plus a soft drop shadow — never a border
- `[Bracket]` monospace labels function as the heading itself, not as a kicker sitting above a larger headline
- Real extracted audio features set as monospace `//`-joined metadata lines, tabular-aligned

## Colors

Near-black warm neutrals carry almost the entire surface; a single amber accent is the system's only saturated color, and it is metered deliberately.

### Primary
- **Amber Accent** (#d4a03c): the system's one accent. Used on the primary button's border/glow, the map's selected/hovered node, `[Bracket]` section labels, focus rings, and "needs attention" status text. Never used for cluster identity or as a fill on large surfaces.
- **Amber Bright** (#e8b84e): the accent's hover-state lift — primary button border and selected-node stroke brighten to this on interaction.

### Neutral
- **Warm Black Base** (#0a0907): page background. Deliberately not pure black — carries a faint warm cast.
- **Warm Black Surface** (#100f0d): secondary background layer — section backgrounds, input fields, inactive tab wells.
- **Warm Black Card** (#161412): elevated surface — cards, panels, active tab, tooltips.
- **Warm Black Panel** (#141210): reserved panel-background step between surface and card.
- **Warm Black Media** (#0e0c0a): the map canvas and empty-state well — the darkest interactive surface, distinct from static card backgrounds.
- **Ivory Primary** (#f0ebe0): primary text — headings, song titles, active states.
- **Ivory Body** (#a89e8c): secondary text — descriptions, artist names, body copy.
- **Ivory Muted** (#6a6357) / **Ivory Tertiary** (#6b6355): de-emphasized text — muted headline words, `[Bracket]` labels' surrounding metadata, disabled-adjacent captions.
- **Border Subtle** (rgba(255,248,230,0.07)) / **Border Medium** (rgba(255,248,230,0.13)): hairline dividers and default input/tab borders. Never used to outline cards.
- **Border Accent** (rgba(212,160,60,0.55)): focus-state border on inputs and the featured-card outline.

### Signal colors
- **Success** (#4fb571): placed pipeline state, synced status.
- **Error** (#d1554f): unresolved / extraction-failed pipeline state.
- **Info** (#6a9fd4): resolving pipeline state.

### Named Rules
**The Surgical Amber Rule.** The accent color is spent only on primary actions, selection, and live/attention state — never on cluster identity, decoration, or a large fill. `TasteMap.tsx`'s `CLUSTER_COLORS` palette is intentionally desaturated warm-neutral and deliberately excludes the accent hue so that group identity reads through hue, and selection/hover reads through the one reserved amber.

**The No-Pure-Black Rule.** Every "black" surface in the system carries a warm cast (#0a0907 and its stepped siblings) rather than true black — the near-black room, not a void.

## Typography

**Display/Body Font:** Inter (with system-ui, sans-serif fallback)
**Label/Mono Font:** JetBrains Mono (with ui-monospace, monospace fallback)

**Character:** Inter carries structure and prose at a restrained, tightly-tracked weight; JetBrains Mono is reserved entirely for technical and status text — section labels, metadata readouts, pipeline states — so the mono voice always signals "this is measured, not written."

### Hierarchy
- **Display** (700, clamp(40px, 7vw, 88px), line-height 1.05, tracking -0.03em): the signed-out hero headline only. The one place in the system where type gets large.
- **Title** (600, 16–18px, line-height ~1.2, tracking -0.02em): card and panel headings — song title in the selected-song panel, empty-state titles, landing step titles.
- **Body** (400, 13–16px, line-height 1.6): descriptions, list rows, form labels, status prose.
- **Label** (400, 10–11px, JetBrains Mono, tracking 0.04–0.06em, often uppercase): `[Bracket]` section labels, pipeline state text, table column headers, metadata lines, "ADDED VIA ___" captions.
- **Caption** (400, 12px, Inter, line-height 1.5): a step below Body for dense secondary/tertiary UI text where Operate-mode density matters more than reading comfort — table data cells (artist names, genre, secondary rows), compact tab labels, footnotes. Not for prose a visitor is meant to read at length; that stays in Body.

Note: `globals.css` declares a full numeric type-scale (`--text-display`, `--text-h1/h2/h3`, `--text-sm`, `--text-label`) that is not referenced by any component — every component sets its own literal pixel/clamp value instead. The hierarchy above reflects what the built components actually use, not the unused token scale; treat the declared `--text-*` custom properties as dormant until a future pass adopts them.

### Named Rules
**The Mono-Means-Measured Rule.** JetBrains Mono is used exclusively for technical/status/label text (section labels, metadata, pipeline states) — never for headings or prose. If it's set in mono, it's data or a system label; if it's Inter, it's written for a person.

## Layout

The signed-in app runs a fixed three-column grid at desktop width (`300px / 1fr / 300px`, `lg:grid-cols-[300px_1fr_300px]`) inside a `1400px` max-width container: left column is ingestion + pipeline status (sticky), center is the map/table plus its status bar, right column is the selected-song panel and recommendations. Below the `lg` breakpoint the columns stack to a single flow. A fixed, scroll-aware blurred nav (16px backdrop blur, border fades in past an 8px scroll threshold) sits above everything at a 64px height.

The signed-out Landing page breaks from the grid entirely: a single centered column, a staged `reveal-up` entrance (label → headline → body → CTA, each offset by ~120ms), a bottom-anchored breathing radial-gradient orb, a scroll-progressive "mechanism" section, a reskinned three-step section, and a closing bookend CTA — the one surface allowed the Persuade register, and the one surface that carries the Fraunces serif.

**Landing-only typography.** Fraunces (variable, optical sizing + italic axis) sets the hero headline, the mechanism section's revealing sentence, and the three-step numerals/titles — nowhere else in the system. Roman weight carries the base sentence; italic + `--accent-bright` marks the one or two words per line that are the actual point (`it *sounds*`, `*tempo*`, `*key*`, `*energy*`, `*timbre*`, `*genre tag*`, `*someone else streamed*`), directly translating the pinned reference's high-contrast-serif-plus-italic-accent-word move into sonicmap's real vocabulary — the italicized words are never invented flavor text, always the actual measured features or the thing they're contrasted against. Every signed-in surface stays Inter/JetBrains Mono; Fraunces never leaves Landing.

**The 3D brain motif (signature Landing component, `LandingBrain.tsx`).** The same brain-of-clusters visual built for the signed-in dashboard's Brain view (`TasteBrain.tsx` — nodes projected onto a procedural brain-shaped surface, connected to their nearest same-cluster neighbor, ambient particles tinted per cluster, a soft bloom-lit `Aura` shell) is reused here decoratively, replacing the earlier flat SVG constellation and an even earlier generated-video attempt. Synthetic node positions only (`LandingBrain.tsx`'s own hand-shaped `NODES`/`EDGES`, connected-components → cluster labels) — never a real user's map data, this is a signed-out visitor with none yet — but built from the *actual* rendering code the product uses elsewhere, not a lookalike. The camera dollies and orbits continuously, driven directly by scroll progress (a ref read every R3F frame, never React state) rather than drag/OrbitControls — scrolling *is* the camera move.

**Cinematic scroll (GSAP ScrollTrigger + Lenis).** Landing is the one surface in the system built on the real technique rather than a static imitation of it: Lenis eases native scroll input, and GSAP's ScrollTrigger drives the scrub-linked timelines — one page-length trigger keeps a `scrollProgressRef` current for the 3D brain's camera (`LandingBrain.tsx`'s `ScrollCamera`, radius 5.2→2.5 and an orbit angle tied to progress, plus a slow idle drift so it never looks frozen between scrolls), and the mechanism section's sentence reveals word-by-word while pinned for one viewport's worth of scroll (`pin: true`, `scrub`), a real cinematic hold rather than a tall div the reader scrolls past. All of it is dynamically initialized in a `useEffect` and fully skipped under `prefers-reduced-motion` — reduced-motion visitors get plain native scroll with every section already visible, no pinning, no Lenis easing, and the brain's camera and auto-rotation both stay static since they read the same `reduceMotion` check.

**One continuous background, never a per-section swap.** The 3D brain canvas, a dark scrim gradient for text legibility, and the radial-gradient orb all live stacked in a single `position: fixed` layer behind every section — no section carries its own background color or a `border-t` seam; the shared fixed layer is what makes scrolling read as one continuous space rather than a stack of independent blocks. Section-local surfaces (the three-step grid) get their separation from a translucent backdrop-blurred card, not a background-color change.

Spacing follows an 8-step scale (4/8/12/16/20/24/32/40/48/64px, `--sp-1` through `--sp-16`); card internal padding is consistently 24px (`p-6`).

**Stated constraint:** `SongTable.tsx`'s responsive behavior is a horizontally-scrolling dense table (`overflow-x-auto`, `min-w-[720px]`) with a static right-edge fade affordance on mobile, not a card-per-row mobile layout. This was a deliberate Operate-mode density choice, reviewed and shipped as-is — it is not a defect to fix, but it is also not evidence that other data-dense surfaces should default to horizontal scroll; treat it as a stated, scoped tradeoff for this table.

## Elevation & Depth

The system uses a hybrid: layered near-black surfaces for depth position (base → surface → card, each a measured step lighter) plus a consistent shadow pair on every raised surface — an inset top highlight (simulating a light catching the top edge) and an outer ambient drop shadow. No card, panel, or interactive surface ever carries a visible border as its elevation cue.

### Shadow Vocabulary
- **Card rest** (`inset 0 1px 0 rgba(255,248,230,0.08), 0 4px 24px rgba(0,0,0,0.45)`): default state for every `Card`, tooltip, and the song table.
- **Card hover** (`inset 0 1px 0 rgba(255,248,230,0.1), 0 12px 40px rgba(0,0,0,0.55)`): `hoverable` cards lift on hover with a deeper, softer shadow plus a 2px translate.
- **Card featured** (adds `0 0 0 1px var(--border-accent), 0 0 30px rgba(212,160,60,0.12)` to the rest shadow): the selected-song panel's outlined-in-light treatment — the one card variant allowed an accent-colored halo.
- **Primary glow** (`0 0 8px rgba(212,160,60,0.5), 0 0 20px rgba(212,160,60,0.22), 0 0 40px rgba(212,160,60,0.09)`, pulsing via `btn-pulse` 2.8s): the primary CTA and the Landing sign-in link. Intensifies on hover and stops pulsing once hovered.
- **Selected-node glow** (`drop-shadow(0 0 4–9px rgba(212,160,60,0.55–0.85))`, pulsing via `node-glow-pulse` 1.8s): the map's selected point.

### Named Rules
**The One Glow Rule.** The pulsing amber glow is reserved for exactly two things system-wide: the primary call-to-action button and the selected node on the taste map. Both signal "this is the one live/actionable thing here" — the glow never decorates a static element.

**The Inset-Not-Border Rule.** Elevation is always conveyed by an inset highlight plus an outer shadow, never by an outlined border. Borders (`--border-subtle`, `--border-medium`) are reserved for dividers, input strokes, and tab wells — not for card containment.

## Shapes

Corners are consistently rounded and scale with surface size: 6px for small controls (icon buttons, zoom controls), 10px for standard interactive elements (buttons, inputs, tabs), 16px for cards and the map/table container, 20px reserved as an unused upper step, and a full pill (999px) for badges and the status chip. Nothing in the system uses a sharp (0px) corner or a hard geometric silhouette — the world is softly rounded throughout, consistent with its "instrument panel," not neobrutalist, register. A fixed film-grain SVG-noise overlay (3.5% opacity, overlay blend mode) sits over the entire viewport as the system's one recurring texture device.

## Components

### Buttons
- **Shape:** 10px radius (`--r-md`) at every size.
- **Primary:** warm-black-card background, amber-accent (#d4a03c) 1px border, ivory-primary text, permanently pulsing amber glow (see Elevation). Padding 12px/24px default, 8px/18px small. Reserved for the one primary action per view (Rebuild map, Sign in with Spotify).
- **Secondary:** same warm-black-card background, border-medium (neutral) border instead of accent — no glow. Default choice for form-submit and non-primary actions.
- **Ghost:** transparent background and border, ivory-body text that brightens to ivory-primary on hover with a faint white-wash background. Used for icon-only utility actions (refresh, sign out, close).
- **Hover / Focus:** all variants lift 1px (`-translate-y-px`) on hover; primary's glow intensifies and stops pulsing on hover. Focus-visible everywhere gets a 2px solid amber outline with 2px offset.

### Cards / Containers
- **Corner Style:** 16px radius (`--r-lg`).
- **Background:** warm-black-card (#161412).
- **Shadow Strategy:** inset highlight + outer shadow at rest; see Elevation & Depth. No border.
- **Internal Padding:** 24px (`p-6`), consistent across every panel (Ingest, Pipeline, Recommendations, Selected Song).

### Inputs / Fields
- **Style:** warm-black-surface background, border-medium (neutral) 1px stroke, 10px radius, ivory-primary text with tertiary-colored placeholder.
- **Focus:** border shifts to border-accent (amber, 55% opacity) — no glow, no ring, just the border color change.

### Navigation
- Fixed, full-width, 64px tall. Transparent with no border at the top of scroll; past an 8px scroll threshold it gains a subtle border-bottom and a translucent near-black background with 16px backdrop blur. Wordmark is set in JetBrains Mono with a single amber accent period. Sign-out is a ghost icon button.

### Section Label / `[Bracket]` heading (signature component)
`SectionLabel` renders its children wrapped in literal square brackets, in 11px JetBrains Mono, amber accent color, 0.06em tracking. It is used as the heading itself for a panel (`[Pipeline]`, `[Recommendations]`, `[Ingest]`) — nothing larger sits above or below it in a kicker relationship. Where a panel needs a true title larger than the label (e.g. a song name in the selected-song panel), the bracket label is dropped entirely rather than stacked above the larger heading.

### MetaLine (signature component)
Renders a song's real Essentia-extracted features as a single monospace line, fields joined by `" // "` (e.g. `128 BPM // C maj // ENERGY 0.82 // DANCE 0.71`), tabular-numeral aligned so stacked readouts don't jitter. This is the system's literal, ground-truth translation of the reference world's invented technical-metadata trope: every field is a real measurement, never generated flavor text. Falls back to `NO FEATURES YET` in tertiary color when extraction hasn't run.

### Icons
Line-style icons from `lucide-react` at 13–20px, consistently `strokeWidth={1.5}`, colored to match surrounding text (never accent unless indicating a live/selected state). Used only as functional glyphs inside buttons, tabs, and status chips — never as decorative or purely illustrative elements.

## Do's and Don'ts

### Do:
- **Do** reserve the amber accent (#d4a03c) for primary actions, selection, and live/attention state only — check any new accent-colored element against those three roles before shipping it.
- **Do** raise every card and panel with the inset-highlight-plus-shadow pair; never add a border to signal elevation.
- **Do** set all technical/status/data text in JetBrains Mono; keep prose and headings in Inter.
- **Do** treat a `[Bracket]` label as a heading in its own right when it's the top of a panel — it does not need a larger headline above or below it.
- **Do** keep MetaLine and any future technical-readout component tied to real extracted values; the mono-metadata pattern only earns its place because the numbers are real.

### Don't:
- **Don't** stack a `[Bracket]` label directly above a larger heading as a kicker — this was shipped in an earlier pass on Landing, IngestPanel, and SelectedSongPanel and corrected in review. The bracket label either is the heading, or it's dropped in favor of the larger one; it never sits above it announcing it.
- **Don't** use the amber accent for cluster/category identity — `TasteMap.tsx`'s cluster palette is deliberately desaturated and accent-free so the one accent hue stays legible as "selected/live," not "category 3."
- **Don't** introduce a bordered-card treatment — every elevated surface in the shipped system uses the inset+shadow pair, never an outline.
- **Don't** treat `SongTable`'s horizontal-scroll mobile pattern as a general mobile-table default without re-evaluating; it was a scoped density decision for that specific dense table, not a system-wide responsive rule.
