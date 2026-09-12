"use client";

import { Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { Brain, LayoutGrid, RefreshCw, Table2, Trash2 } from "lucide-react";
import { apiFetch, clearSessionToken, getSessionToken, googleLoginUrl, spotifyLoginUrl } from "@/lib/api";
import { CatalogStatus, CurrentUser, MapPoint, Recommendation, StatusMessage, StemJob, StemXRay, pipelineState } from "@/lib/types";
import type { ResonanceThreadInfo } from "@/components/TasteBrain";
import { Nav } from "@/components/Nav";
import { Landing } from "@/components/Landing";
import { IngestPanel } from "@/components/IngestPanel";
import { PipelineRail } from "@/components/PipelineRail";
import { TasteMap } from "@/components/TasteMap";
import { SongTable } from "@/components/SongTable";
import { RecommendationsPanel } from "@/components/RecommendationsPanel";
import { DiscoveryStudio } from "@/components/DiscoveryStudio";
import { SelectedSongPanel } from "@/components/SelectedSongPanel";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/Button";
import { SystemStatus } from "@/components/ui/StatusBadge";
import { StatsBar } from "@/components/ui/StatsBar";
import { JUST_SIGNED_IN_KEY } from "@/app/auth/callback/page";

// Three.js/WebGL needs a real browser — loaded only when the Brain view is actually opened,
// never during SSR or on first page load for people who never switch to it.
const TasteBrain = dynamic(() => import("@/components/TasteBrain").then((m) => m.TasteBrain), {
  ssr: false,
  loading: () => (
    <div className="flex h-[560px] items-center justify-center rounded-[var(--r-lg)] bg-[var(--bg-media)]">
      <RefreshCw size={18} strokeWidth={1.5} className="animate-spin text-[var(--text-tertiary)]" />
    </div>
  ),
});

const SignInBrainIntro = dynamic(
  () => import("@/components/SignInBrainIntro").then((m) => m.SignInBrainIntro),
  {
    ssr: false,
    // Never null: this can be on screen for a real (if small) window while the chunk fetches
    // on a cold cache, and "nothing" here means whatever's rendered underneath — the plain
    // sign-in spinner — bleeds through instead of the intended solid cinematic backdrop. A
    // static div in the exact same color the real intro opens on makes that window invisible
    // instead of a flash of the wrong screen.
    loading: () => <div className="fixed inset-0 z-[100] bg-[var(--bg-base)]" aria-hidden />,
  }
);

type View = "map" | "list" | "brain";

function Dashboard() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [user, setUser] = useState<CurrentUser | null | "loading">("loading");
  const [mapPoints, setMapPoints] = useState<MapPoint[]>([]);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Signed-in visitors land on the 3D brain view by default — the most distinctive way to
  // first see a freshly-built map, with Map/List still one click away for anyone who prefers
  // (or needs, per PRODUCT.md's accessibility commitment) the non-spatial or 2D view.
  const [view, setView] = useState<View>("brain");
  // Set when a recommendation's "show in brain" is clicked — TasteBrain consumes it once to
  // animate a pulse from the matched cluster, then this clears back to null via onPulseComplete.
  const [pulseFromSongId, setPulseFromSongId] = useState<string | null>(null);
  // The one active Resonance Threads exploration, arrived at from Stem Studio's per-stem "view
  // in brain" (see the `?resonance=` handling below). Unlike pulseFromSongId this is *not*
  // consumed-once — threads stay drawn until the visitor explicitly clears them, since the
  // whole point is sitting with a cross-cluster connection, not being shown it for a second.
  const [resonance, setResonance] = useState<{
    sourceSongId: string;
    stemLabel: string;
    threads: ResonanceThreadInfo[];
  } | null>(null);
  const [status, setStatus] = useState<StatusMessage>(null);
  const [ingesting, setIngesting] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [clearingAll, setClearingAll] = useState(false);
  const [confirmingClearAll, setConfirmingClearAll] = useState(false);
  const [refreshingRecs, setRefreshingRecs] = useState(false);
  // Server-side truth, not a client-guessed "you did something, click rebuild" flag — the
  // backend now auto-triggers a rebuild after every ingest that changes the map (see
  // backend/app/services/brain.py's schedule_rebuild), so this just reflects reality.
  const [mapRebuildStatus, setMapRebuildStatus] = useState<"idle" | "processing" | "failed">("idle");
  const prevRebuildStatusRef = useRef<"idle" | "processing" | "failed">("idle");
  const [mapLoading, setMapLoading] = useState(true);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [seenRecIds, setSeenRecIds] = useState<string[]>([]);
  const [recsExhausted, setRecsExhausted] = useState(false);
  const [englishOnly, setEnglishOnly] = useState(false);
  const [catalogStatus, setCatalogStatus] = useState<CatalogStatus | null>(null);
  const catalogVersionRef = useRef<number | null>(null);
  // Set only right after a fresh Spotify sign-in (auth/callback/page.tsx), consumed once here
  // so a manual reload of "/" never replays it. Starts false (matching the server-rendered
  // HTML — sessionStorage doesn't exist there, so any read-in-initializer approach here would
  // hydration-mismatch: server always renders false, client can render true). useLayoutEffect,
  // not useEffect: it still runs synchronously — same "intentionally synchronous, not deferred
  // via setTimeout" reasoning as before, so StrictMode's dev-only mount→cleanup→remount can't
  // race a pending timeout against the removeItem the way it did previously — but it also runs
  // *before* the browser paints, so the resulting re-render (with the intro visible) happens
  // before anything without it is ever shown on screen. That's what actually removes the old
  // "bare spinner" flash, not the hydration-mismatch shortcut a lazy initializer looked like it
  // would give.
  const [showSignInIntro, setShowSignInIntro] = useState(false);

  useLayoutEffect(() => {
    if (sessionStorage.getItem(JUST_SIGNED_IN_KEY) !== "1") return;
    sessionStorage.removeItem(JUST_SIGNED_IN_KEY);
    // This is intentionally pre-paint; deferring it reintroduces the authenticated spinner
    // flash that SignInBrainIntro exists to cover.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setShowSignInIntro(true);
  }, []);

  // Prefetch the intro's chunk (and the R3F/Three.js machinery it shares with TasteBrain)
  // immediately on mount, not just when this page happens to need it — this is the dashboard
  // route the OAuth round trip lands back on, and by the time that happens there's been no
  // opportunity to warm this up unless it starts here too, in parallel with Landing.tsx's own
  // earlier prefetch on the same chunk (whichever visitor path led here).
  useEffect(() => {
    void import("@/components/SignInBrainIntro");
  }, []);

  const refreshMap = useCallback(async () => {
    const points = await apiFetch<MapPoint[]>("/map");
    setMapPoints(points);
    setMapLoading(false);
  }, []);

  const refreshRebuildStatus = useCallback(async () => {
    try {
      const { status: s } = await apiFetch<{ status: "idle" | "processing" | "failed" }>("/map/status");
      setMapRebuildStatus(s);
    } catch {
      // Best-effort — the polling loop just retries on its next tick.
    }
  }, []);

  const refreshCatalogStatus = useCallback(async () => {
    try {
      const next = await apiFetch<CatalogStatus>("/catalog/status");
      const previous = catalogVersionRef.current;
      catalogVersionRef.current = next.version;
      setCatalogStatus(next);
      return previous !== null && next.version > previous;
    } catch {
      return false;
    }
  }, []);

  const refreshRecs = useCallback(async (englishOnlyOverride?: boolean) => {
    setRefreshingRecs(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", "30");
      if (englishOnlyOverride ?? englishOnly) params.set("english_only", "true");
      const results = await apiFetch<Recommendation[]>(`/recommendations?${params.toString()}`);
      setRecs(results);
      setSeenRecIds(results.map((r) => r.song.id));
      setRecsExhausted(false);
    } catch (err) {
      setStatus({ message: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setRefreshingRecs(false);
    }
  }, [englishOnly]);

  const getMoreRecs = useCallback(async () => {
    setRefreshingRecs(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", "30");
      for (const id of seenRecIds) params.append("exclude", id);
      if (englishOnly) params.set("english_only", "true");
      const results = await apiFetch<Recommendation[]>(`/recommendations?${params.toString()}`);
      if (results.length === 0) {
        // Every close match in the catalog has already been shown this session — keep the
        // current list on screen (an empty panel would look broken, not "caught up") and
        // say so instead.
        setRecsExhausted(true);
      } else {
        setRecs(results);
        setSeenRecIds((prev) => [...prev, ...results.map((r) => r.song.id)]);
      }
    } catch (err) {
      setStatus({ message: err instanceof Error ? err.message : String(err), tone: "error" });
    } finally {
      setRefreshingRecs(false);
    }
  }, [seenRecIds, englishOnly]);

  async function handleLanguageChange(next: boolean) {
    setEnglishOnly(next);
    await refreshRecs(next);
  }

  async function handleHideRecommendation(songId: string) {
    // Optimistic — a dismissed song leaving the list immediately matters more here than
    // waiting on the round trip, and the backend call is a permanent record either way.
    setRecs((prev) => prev.filter((r) => r.song.id !== songId));
    await withStatus(async () => {
      await apiFetch(`/recommendations/${songId}/hide`, { method: "POST" });
    });
  }

  function handleShowInBrain(bestMatchSongId: string) {
    setView("brain");
    setPulseFromSongId(bestMatchSongId);
  }

  // Consumes a `?pulse=<songId>` deep link — the cross-page version of handleShowInBrain above,
  // landed on from Stem Studio's own "Show in brain" actions (a separate route, so it can't set
  // this component's state directly; see app/studio/stems/page.tsx's showInBrain). Cleared from
  // the URL immediately so a reload or share of the link doesn't replay the pulse forever, but
  // pulseFromSongId itself stays set — TasteBrain looks the id up in `nodes` on every render, so
  // it still finds and pulses the node once mapPoints finishes loading, even if that happens
  // after this effect runs.
  useEffect(() => {
    const pulse = searchParams.get("pulse");
    if (!pulse) return;
    // Deferred a microtask so the state lands in a promise callback rather than the effect
    // body — same reasoning (and same rule) as checkAuth below.
    Promise.resolve().then(() => {
      setView("brain");
      setPulseFromSongId(pulse);
      router.replace("/");
    });
  }, [searchParams, router]);

  // Consumes a `?resonance=<stemJobId>&stem=<layerName>` deep link from Stem Studio's per-stem
  // "view in brain" (app/studio/stems/page.tsx's viewResonance). The threads themselves are
  // re-fetched here rather than passed through the URL: they're computed against the visitor's
  // *current* map (backend/app/services/stem_intelligence.py's build_xray), so a link followed
  // after adding or removing songs should reflect the map as it is now, not as it was when the
  // Stem Studio tab last rendered. Gated on a signed-in user because /stems is authenticated —
  // and deliberately does not clear the URL until it can actually act, so a link opened in a
  // cold tab survives the auth check resolving.
  useEffect(() => {
    const jobId = searchParams.get("resonance");
    const stemName = searchParams.get("stem");
    if (!jobId || !stemName || !user || user === "loading") return;
    let cancelled = false;
    Promise.resolve()
      .then(() => {
        setView("brain");
        router.replace("/");
        return Promise.all([
          apiFetch<StemJob>(`/stems/jobs/${jobId}`),
          apiFetch<StemXRay>(`/stems/jobs/${jobId}/xray`),
        ]);
      })
      .then(([job, xray]) => {
        if (cancelled) return;
        const stem = xray.stems.find((row) => row.name === stemName);
        if (!job.mapped_song_id) {
          setStatus({ message: "Add that song to your map first — resonance threads draw from its node.", tone: "error" });
          return;
        }
        if (!stem) {
          setStatus({ message: "That stem layer is no longer part of this session.", tone: "error" });
          return;
        }
        if (stem.threads.length === 0) {
          // Not an error: build_xray only keeps matches above its confidence floor, so "none"
          // is a real answer about this layer, and saying so beats a silently empty brain.
          setStatus({ message: `No song on your map resonates with this track's ${stem.label.toLowerCase()} yet.`, tone: "neutral" });
          return;
        }
        setResonance({
          sourceSongId: job.mapped_song_id,
          stemLabel: stem.label,
          threads: stem.threads.map((thread) => ({
            targetSongId: thread.song_id,
            label: `${thread.title} — ${thread.artist}`,
            match: thread.match,
          })),
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus({ message: err instanceof Error ? err.message : String(err), tone: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, [searchParams, router, user]);

  const checkAuth = useCallback(() => {
    // Resolved via a microtask (rather than a synchronous branch) so both the "no token"
    // and "has token" paths set state from a promise callback, not the effect body directly.
    Promise.resolve(getSessionToken()).then((token) => {
      if (!token) {
        setUser(null);
        return;
      }
      return apiFetch<CurrentUser>("/auth/me")
        .then((u) => {
          setUser(u);
          void refreshMap();
          void refreshRecs();
          void refreshRebuildStatus();
          void refreshCatalogStatus();
        })
        .catch(() => {
          clearSessionToken();
          setUser(null);
        });
    });
  }, [refreshMap, refreshRecs, refreshRebuildStatus, refreshCatalogStatus]);

  useEffect(() => {
    if (!catalogStatus?.enrichment_active) return;
    const interval = setInterval(() => {
      void refreshCatalogStatus().then((advanced) => {
        if (advanced) void refreshRecs();
      });
    }, 10000);
    return () => clearInterval(interval);
  }, [catalogStatus?.enrichment_active, refreshCatalogStatus, refreshRecs]);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  useEffect(() => {
    // Ingestion resolves/extracts in the background (backend/app/routers/ingest.py) and a map
    // rebuild is now auto-triggered afterward (backend/app/services/brain.py's
    // schedule_rebuild) — poll while either is in flight, otherwise a song sits visibly stuck
    // on "Resolving"/"Extracting", or the map sits stale, until some unrelated action happens
    // to call refreshMap.
    const isProcessing = mapPoints.some((p) => {
      const s = pipelineState(p);
      return s === "resolving" || s === "extracting";
    });
    if (!isProcessing && mapRebuildStatus !== "processing") return;
    const interval = setInterval(() => {
      void refreshMap();
      void refreshRebuildStatus();
    }, 3000);
    return () => clearInterval(interval);
  }, [mapPoints, mapRebuildStatus, refreshMap, refreshRebuildStatus]);

  useEffect(() => {
    // Detects the processing -> idle/failed transition (rather than just reacting to
    // mapRebuildStatus directly) so this fires exactly once per rebuild, not on every
    // unrelated re-render — a rebuild is now often fast enough (well under a second on a
    // warm server, see backend/app/main.py's startup JIT warmup) that the very first poll
    // after triggering one may already show it finished.
    const prev = prevRebuildStatusRef.current;
    prevRebuildStatusRef.current = mapRebuildStatus;
    if (prev !== "processing" || mapRebuildStatus === "processing") return;

    const message =
      mapRebuildStatus === "failed" ? "Rebuilding your map failed — try again in a moment." : "Map rebuilt.";
    const tone = mapRebuildStatus === "failed" ? "error" : "neutral";
    setRebuilding(false);
    setStatus({ message, tone });
    void refreshMap();
    void refreshRecs();
  }, [mapRebuildStatus, refreshMap, refreshRecs]);

  async function withStatus(action: () => Promise<unknown>) {
    try {
      await action();
    } catch (err) {
      setStatus({ message: err instanceof Error ? err.message : String(err), tone: "error" });
    }
  }

  async function handleSearch(query: string) {
    setIngesting(true);
    setStatus({ message: `Adding "${query}"…`, tone: "neutral" });
    await withStatus(async () => {
      await apiFetch("/ingest/search", { method: "POST", body: JSON.stringify({ query }) });
      // The map is placed automatically once resolution/extraction finishes — no separate
      // "click Rebuild" step. The polling effect below picks up completion from here.
      setStatus({ message: "Added — resolving and placing on your map now.", tone: "neutral" });
      await refreshMap();
      // The song is excluded from recommendations the moment it's linked to the user (the
      // exclusion is by song id, set at ingest — it doesn't wait on background extraction),
      // so refreshing now keeps the panel from still showing something just added.
      await refreshRecs();
      await refreshRebuildStatus();
    });
    setIngesting(false);
  }

  async function handlePaste(raw: string) {
    setIngesting(true);
    setStatus({ message: "Adding pasted list…", tone: "neutral" });
    await withStatus(async () => {
      const results = await apiFetch<unknown[]>("/ingest/paste", {
        method: "POST",
        body: JSON.stringify({ raw_text: raw }),
      });
      setStatus({ message: `Added ${results.length} songs — resolving and placing on your map now.`, tone: "neutral" });
      await refreshMap();
      await refreshRecs();
      await refreshRebuildStatus();
    });
    setIngesting(false);
  }

  async function handlePlaylist(url: string) {
    setIngesting(true);
    setStatus({ message: "Importing Spotify playlist…", tone: "neutral" });
    await withStatus(async () => {
      const results = await apiFetch<unknown[]>("/ingest/spotify-playlist", {
        method: "POST",
        body: JSON.stringify({ playlist_id_or_url: url }),
      });
      setStatus({ message: `Imported ${results.length} songs — resolving and placing on your map now.`, tone: "neutral" });
      await refreshMap();
      await refreshRecs();
      await refreshRebuildStatus();
    });
    setIngesting(false);
  }

  async function handleRebuild() {
    // Non-blocking on the backend (see backend/app/routers/map.py) — this kicks the rebuild
    // off and returns almost immediately; the polling effect + the transition effect above
    // handle detecting completion and clearing `rebuilding`, since the response here can't
    // say how long the actual work will take.
    setRebuilding(true);
    setStatus({ message: "Rebuilding your taste map…", tone: "neutral" });
    try {
      const { status: s } = await apiFetch<{ status: "idle" | "processing" | "failed" }>(
        "/map/rebuild",
        { method: "POST" }
      );
      setMapRebuildStatus(s);
    } catch (err) {
      setStatus({ message: err instanceof Error ? err.message : String(err), tone: "error" });
      setRebuilding(false);
    }
  }

  async function handleClearAll() {
    setClearingAll(true);
    setConfirmingClearAll(false);
    await withStatus(async () => {
      const { removed } = await apiFetch<{ removed: number }>("/map", { method: "DELETE" });
      setSelectedId(null);
      setStatus({ message: `Cleared ${removed} song${removed === 1 ? "" : "s"} from your map.`, tone: "neutral" });
      await refreshMap();
      await refreshRecs();
    });
    setClearingAll(false);
  }

  async function handleRemove(songId: string) {
    setRemovingId(songId);
    await withStatus(async () => {
      await apiFetch(`/map/${songId}`, { method: "DELETE" });
      setSelectedId(null);
      setStatus({ message: "Removed from your map.", tone: "neutral" });
      await refreshMap();
      await refreshRecs();
    });
    setRemovingId(null);
  }

  function handleSignOut() {
    clearSessionToken();
    setUser(null);
    setMapPoints([]);
    setRecs([]);
    setMapLoading(true);
  }

  const placedCount = useMemo(
    () => mapPoints.filter((p) => pipelineState(p) === "placed").length,
    [mapPoints]
  );
  const unplacedReady = useMemo(
    () => mapPoints.some((p) => pipelineState(p) === "unplaced"),
    [mapPoints]
  );
  const selectedPoint = mapPoints.find((p) => p.song.id === selectedId) ?? null;
  // "Ready" once the sign-in outcome is known and, if signed in, the map has loaded — this is
  // what SignInBrainIntro waits on before its fade-out plays, so it always lands directly on
  // the finished page rather than a second loading state underneath.
  const dashboardReady = user !== "loading" && (user === null || !mapLoading);
  const signInIntro = showSignInIntro && (
    <SignInBrainIntro ready={dashboardReady} onComplete={() => setShowSignInIntro(false)} />
  );

  if (user === "loading") {
    return (
      <>
        <div className="flex min-h-screen items-center justify-center bg-[var(--bg-base)] text-[var(--text-body)]">
          <RefreshCw size={18} strokeWidth={1.5} className="animate-spin" />
        </div>
        {signInIntro}
      </>
    );
  }

  if (user === null) {
    return (
      <>
        <Landing spotifyUrl={spotifyLoginUrl()} googleUrl={googleLoginUrl()} />
        {signInIntro}
      </>
    );
  }

  const mapIsEmpty = mapPoints.length === 0;
  // Genuine anomaly, not the common case: the backend auto-triggers a rebuild after every
  // ingest that changes the map, so a song sitting extracted-but-unplaced while nothing is
  // actively processing means that auto-trigger didn't fire or its rebuild failed — the
  // manual "Rebuild map" button is the deliberate fallback for exactly this.
  const needsAttention = unplacedReady && mapRebuildStatus !== "processing";

  return (
    <>
    <div className="min-h-screen bg-[var(--bg-base)]">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <Nav user={user} onSignOut={handleSignOut} />

      <main id="main-content" className="mx-auto max-w-[1400px] px-6 pb-16 pt-24">
        <h1 className="sr-only">Your taste map</h1>
        <div className="grid gap-6 lg:grid-cols-[300px_1fr_300px] lg:items-start">
          <aside
            aria-label="Sonicmap tools"
            className="space-y-6 lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7rem)] lg:overflow-y-auto lg:overscroll-contain lg:pr-2 [scrollbar-gutter:stable]"
          >
            <IngestPanel
              onSearch={handleSearch}
              onPaste={handlePaste}
              onPlaylist={handlePlaylist}
              busy={ingesting}
              status={status}
              spotifyEnabled={Boolean(user.spotify_connected && !user.is_guest)}
            />
            <DiscoveryStudio recommendations={recs} spotifyEnabled={Boolean(user.spotify_connected && !user.is_guest)} />
            <PipelineRail points={mapPoints} onRemove={handleRemove} removingId={removingId} />
          </aside>

          <div className="min-w-0 space-y-4">
            {placedCount > 0 && <StatsBar points={mapPoints} />}

            <div className="flex flex-wrap items-center justify-between gap-3">
              <SystemStatus
                ok={!needsAttention && mapRebuildStatus !== "processing" && placedCount > 0}
                label={
                  needsAttention
                    ? "REBUILD NEEDED"
                    : mapRebuildStatus === "processing"
                    ? "PLACING SONGS"
                    : placedCount > 0
                    ? "MAP SYNCED"
                    : "AWAITING SONGS"
                }
              />
              <div className="flex items-center gap-2">
                <div className="flex gap-1 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-card)] p-1">
                  <button
                    onClick={() => setView("map")}
                    aria-pressed={view === "map"}
                    aria-label="Map view"
                    className={`flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] transition-colors ${
                      view === "map" ? "bg-white/[0.06] text-[var(--text-primary)]" : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    <LayoutGrid size={14} strokeWidth={1.5} />
                  </button>
                  <button
                    onClick={() => setView("list")}
                    aria-pressed={view === "list"}
                    aria-label="List view"
                    className={`flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] transition-colors ${
                      view === "list" ? "bg-white/[0.06] text-[var(--text-primary)]" : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    <Table2 size={14} strokeWidth={1.5} />
                  </button>
                  <button
                    onClick={() => setView("brain")}
                    aria-pressed={view === "brain"}
                    aria-label="Brain view — 3D"
                    className={`flex h-9 w-9 items-center justify-center rounded-[var(--r-sm)] transition-colors ${
                      view === "brain" ? "bg-white/[0.06] text-[var(--text-primary)]" : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                    }`}
                  >
                    <Brain size={14} strokeWidth={1.5} />
                  </button>
                </div>
                <Button
                  variant={needsAttention ? "primary" : "secondary"}
                  size="sm"
                  onClick={handleRebuild}
                  loading={rebuilding || mapRebuildStatus === "processing"}
                >
                  Rebuild map
                </Button>
                <button
                  onClick={() => (confirmingClearAll ? handleClearAll() : setConfirmingClearAll(true))}
                  onBlur={() => setConfirmingClearAll(false)}
                  disabled={clearingAll || mapIsEmpty}
                  aria-label={confirmingClearAll ? "Click again to permanently clear your whole map" : "Clear all songs from your map"}
                  className={`flex items-center gap-2 rounded-[var(--r-md)] border px-[18px] py-2 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                    confirmingClearAll
                      ? "border-[var(--error)] bg-[var(--error-subtle)] text-[var(--error)]"
                      : "border-[var(--border-medium)] bg-[var(--bg-card)] text-[var(--text-body)] hover:border-[var(--error)] hover:text-[var(--error)]"
                  }`}
                >
                  <Trash2 size={13} strokeWidth={1.5} />
                  {clearingAll ? "Clearing…" : confirmingClearAll ? "Click again to clear all" : "Clear all"}
                </button>
              </div>
            </div>

            {mapLoading ? (
              <div className="flex h-[420px] items-center justify-center rounded-[var(--r-lg)] bg-[var(--bg-media)]">
                <RefreshCw size={18} strokeWidth={1.5} className="animate-spin text-[var(--text-tertiary)]" />
              </div>
            ) : mapIsEmpty ? (
              <EmptyState
                title="Nothing here yet"
                body="Search for a song, paste a list, or import a playlist to start building your map."
              />
            ) : view === "map" ? (
              placedCount > 0 ? (
                <div className="h-[560px]">
                  <TasteMap points={mapPoints} selectedId={selectedId} onSelect={setSelectedId} />
                </div>
              ) : (
                <EmptyState
                  title="Still processing"
                  body="Your songs are resolving and extracting. The map fills in once the pipeline finishes — check the Pipeline panel."
                />
              )
            ) : view === "brain" ? (
              placedCount > 0 ? (
                <div className="h-[560px]">
                  <TasteBrain
                    points={mapPoints}
                    selectedId={selectedId}
                    onSelect={setSelectedId}
                    pulseFromSongId={pulseFromSongId}
                    onPulseComplete={() => setPulseFromSongId(null)}
                    resonanceSourceId={resonance?.sourceSongId ?? null}
                    resonanceThreads={resonance?.threads}
                    resonanceLabel={resonance?.stemLabel}
                    onClearResonance={() => setResonance(null)}
                  />
                </div>
              ) : (
                <EmptyState
                  title="Still processing"
                  body="Your songs are resolving and extracting. The brain fills in once the pipeline finishes — check the Pipeline panel."
                />
              )
            ) : (
              <SongTable points={mapPoints} selectedId={selectedId} onSelect={setSelectedId} />
            )}
          </div>

          <aside
            aria-label="Song details and recommendations"
            className="space-y-6 lg:sticky lg:top-24 lg:max-h-[calc(100dvh-7rem)] lg:overflow-y-auto lg:overscroll-contain lg:pr-2 [scrollbar-gutter:stable]"
          >
            {selectedPoint && (
              <SelectedSongPanel
                point={selectedPoint}
                onClose={() => setSelectedId(null)}
                onRemove={() => handleRemove(selectedPoint.song.id)}
                removing={removingId === selectedPoint.song.id}
                onSeparateStems={() =>
                  router.push(
                    `/studio/stems?title=${encodeURIComponent(selectedPoint.song.title)}&artist=${encodeURIComponent(selectedPoint.song.artist)}`
                  )
                }
              />
            )}
            <RecommendationsPanel
              recommendations={recs}
              onGetMore={getMoreRecs}
              busy={refreshingRecs}
              exhausted={recsExhausted}
              englishOnly={englishOnly}
              onLanguageChange={handleLanguageChange}
              onHide={handleHideRecommendation}
              onShowInBrain={handleShowInBrain}
              catalogStatus={catalogStatus}
            />
          </aside>
        </div>
      </main>
    </div>
    {signInIntro}
    </>
  );
}


/** Next 16 refuses to prerender a page that reads useSearchParams outside a Suspense
 * boundary — without this the production build fails on this route even though `next dev`
 * serves it happily. The fallback is the same quiet spinner the authenticating state uses,
 * so the boundary is invisible in practice. */
export default function Home() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[var(--bg-base)]">
          <RefreshCw size={18} strokeWidth={1.5} className="animate-spin text-[var(--text-tertiary)]" />
        </div>
      }
    >
      <Dashboard />
    </Suspense>
  );
}
