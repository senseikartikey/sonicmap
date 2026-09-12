import type { Metadata } from "next";
import { Fraunces, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

// Scoped to the signed-out Landing hero only — the one Persuade-register surface in the
// system (see DESIGN.md). Fraunces' optical sizing + italic carries the cinematic-scroll
// reference's high-contrast serif + accent-word italic treatment; every signed-in surface
// stays Inter/JetBrains Mono, untouched.
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: "variable",
  style: ["normal", "italic"],
  axes: ["opsz", "SOFT", "WONK"],
});

export const metadata: Metadata = {
  title: "sonicmap — the shape of your taste",
  description:
    "Sonicmap maps how your songs actually sound alike — real extracted audio features, not collaborative filtering — as an interactive taste map that never resets.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} ${fraunces.variable} h-full antialiased`}
    >
      <body suppressHydrationWarning className="min-h-full flex flex-col">
        {/*
          THESIS: sonicmap is an instrument, not a dashboard or a Wrapped clone — the map is
          the product, everything else feeds it. Real measurements (BPM, key, energy, distance)
          are the design material, not decoration.
          OWN-WORLD: warm near-black (#0a0907, never pure black), one surgical amber accent
          (#d4a03c) reserved for primary actions, selection, and live data; cards raised by
          inset highlight + shadow, never bordered; JetBrains Mono for all technical/data text
          in [Bracket] labels and `//` metadata lines; Inter for structure and prose; grain
          overlay; one signature glow (primary CTA + selected map node) that never goes dark.
          Pinned by the user's own dark-luxury reference (DESIGN.md/SKILL.md + skillsui.app/
          previews/studio.html), translated from Persuade landing grammar to Operate app
          grammar for every signed-in surface; the signed-out hero is the one Persuade-register
          moment.
          STORY: a signed-out visitor understands in one viewport that this maps how songs
          actually sound, not what other listeners streamed, and signs in with Spotify.
          Signed-in, they feed the brain (search/paste/playlist), watch songs move through
          resolve -> extract -> place, then read their own taste as a navigable amber-lit map
          with real per-node metadata, and pull recommendations by proximity.
          FIRST VIEWPORT (signed-out): centered hero, [Mechanism] label, badge pill, two-line
          color-contrast headline, one primary glow CTA, single elliptical orb bottom-center.
          FIRST VIEWPORT (signed-in): fixed blur nav; left rail for ingestion + pipeline status;
          the map dominant, legible before beautiful, hover/selected node glow, live mono
          metadata readout; recommendations reachable without leaving the map.
          FORM: brief-pinned dark-luxury world, no concept-seed roll; code-led build (no image
          generation available this session).
          FINISH: unreviewed and undocumented is unfinished; this build ends with the finish
          review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
        */}
        {children}
      </body>
    </html>
  );
}
