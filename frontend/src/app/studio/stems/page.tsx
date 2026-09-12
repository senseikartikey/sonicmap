"use client";

import { ChangeEvent, DragEvent, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowLeft, AudioLines, Check, Clock3, RefreshCw, Upload, Video } from "lucide-react";
import { apiFetch, clearSessionToken, getSessionToken } from "@/lib/api";
import { CurrentUser, Recommendation, StemJob, StemManifest, StemXRay } from "@/lib/types";
import { Nav } from "@/components/Nav";
import { StemMixer } from "@/components/StemMixer";
import { Button } from "@/components/ui/Button";
import { Card, SectionLabel } from "@/components/ui/Card";

const ACTIVE = new Set(["queued", "acquiring", "transcoding", "separating", "packaging"]);
const MIME_BY_EXTENSION: Record<string, string> = { mp3: "audio/mpeg", m4a: "audio/mp4", aac: "audio/aac", wav: "audio/wav", flac: "audio/flac", ogg: "audio/ogg", opus: "audio/opus" };

function uploadToStorage(url: string, fields: Record<string, string>, file: File, onProgress: (value: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
    request.onload = () => request.status >= 200 && request.status < 300 ? resolve() : reject(new Error("The upload could not be completed."));
    request.onerror = () => reject(new Error("The upload was interrupted. Check your connection and try again."));
    const form = new FormData();
    Object.entries(fields).forEach(([key, value]) => form.append(key, value));
    form.append("file", file);
    request.send(form);
  });
}

function StemStudio() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Deep-link context from a song's "Separate stems" action elsewhere in the app (a brain node,
  // SelectedSongPanel) — this page never auto-fetches on the visitor's behalf (that's the whole
  // reason source acquisition stays a manual upload/YouTube-link step, not a search), so this is
  // purely a "here's what you meant, paste its source below" prompt, not a lookup.
  const requestedSong = useMemo(() => {
    const title = searchParams.get("title");
    const artist = searchParams.get("artist");
    return title ? { title, artist: artist ?? "" } : null;
  }, [searchParams]);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [mode, setMode] = useState<"upload" | "youtube">("upload");
  const [file, setFile] = useState<File | null>(null);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [model, setModel] = useState<"4stem" | "6stem">("4stem");
  const [rights, setRights] = useState(false);
  const [jobs, setJobs] = useState<StemJob[]>([]);
  const [selected, setSelected] = useState<StemJob | null>(null);
  const [manifest, setManifest] = useState<StemManifest | null>(null);
  const [xray, setXray] = useState<StemXRay | null>(null);
  const [mixRecommendations, setMixRecommendations] = useState<Recommendation[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [addingJobId, setAddingJobId] = useState<string | null>(null);
  const [addedJobIds, setAddedJobIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const discoverAbort = useRef<AbortController | null>(null);

  const loadJobs = useCallback(async () => {
    const result = await apiFetch<StemJob[]>("/stems/jobs");
    setJobs(result);
    setSelected((current) => current ? result.find((job) => job.id === current.id) ?? current : result[0] ?? null);
  }, []);

  useEffect(() => {
    Promise.resolve(getSessionToken()).then((token) => {
      if (!token) return;
      return apiFetch<CurrentUser>("/auth/me").then((result) => {
        setUser(result);
        return loadJobs();
      }).catch(() => clearSessionToken());
    }).finally(() => setAuthLoading(false));
  }, [loadJobs]);

  useEffect(() => {
    if (!selected || !ACTIVE.has(selected.status)) return;
    const interval = window.setInterval(() => {
      if (document.hidden) return;
      apiFetch<StemJob>(`/stems/jobs/${selected.id}`).then((next) => {
        setSelected(next);
        setJobs((previous) => previous.map((job) => job.id === next.id ? next : job));
      }).catch(() => {});
    }, 2000);
    return () => window.clearInterval(interval);
  }, [selected]);

  useEffect(() => {
    Promise.resolve().then(() => {
      setManifest(null);
      setXray(null);
      setMixRecommendations([]);
      if (selected?.status !== "ready") return;
      return Promise.all([
        apiFetch<StemManifest>(`/stems/jobs/${selected.id}/manifest`).then(setManifest),
        selected.analysis_ready ? apiFetch<StemXRay>(`/stems/jobs/${selected.id}/xray`).then(setXray) : Promise.resolve(),
      ]).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
    });
  }, [selected?.analysis_ready, selected?.id, selected?.status]);

  function chooseFile(next: File | null) {
    setError(null);
    if (next && next.size > 250 * 1024 * 1024) {
      setError("Audio files must be 250 MB or smaller.");
      return;
    }
    setFile(next);
  }

  async function createJob() {
    setBusy(true);
    setError(null);
    try {
      let payload: Record<string, unknown>;
      if (mode === "upload") {
        if (!file) throw new Error("Choose an audio file first.");
        const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
        const contentType = MIME_BY_EXTENSION[extension] ?? file.type;
        const upload = await apiFetch<{ upload_key: string; upload_url: string; upload_fields: Record<string, string> }>("/stems/uploads", {
          method: "POST", body: JSON.stringify({ filename: file.name, content_type: contentType, size_bytes: file.size }),
        });
        setUploadProgress(0);
        await uploadToStorage(upload.upload_url, upload.upload_fields, file, setUploadProgress);
        payload = { source_type: "upload", upload_key: upload.upload_key, source_name: file.name, model, rights_confirmed: rights };
      } else {
        if (!youtubeUrl.trim()) throw new Error("Enter a YouTube URL first.");
        payload = { source_type: "youtube", youtube_url: youtubeUrl.trim(), source_name: "Fetching YouTube details…", model, rights_confirmed: rights };
      }
      const job = await apiFetch<StemJob>("/stems/jobs", { method: "POST", body: JSON.stringify(payload) });
      setJobs((previous) => [job, ...previous]);
      setSelected(job);
      setFile(null);
      setYoutubeUrl("");
      setUploadProgress(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function cancelJob() {
    if (!selected) return;
    await apiFetch(`/stems/jobs/${selected.id}`, { method: "DELETE" });
    const next = { ...selected, status: "cancelled" as const, stage: "cancelled" };
    setSelected(next);
    setJobs((previous) => previous.map((job) => job.id === next.id ? next : job));
    setManifest(null);
  }

  async function addSelectedToMap() {
    if (!selected?.source_name || addingJobId) return;
    setAddingJobId(selected.id);
    setError(null);
    try {
      await apiFetch(`/stems/jobs/${selected.id}/add-to-map`, { method: "POST" });
      setAddedJobIds((previous) => new Set(previous).add(selected.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAddingJobId(null);
    }
  }

  async function discoverFromMix(weights: Record<string, number>) {
    if (!selected) return;
    discoverAbort.current?.abort();
    const controller = new AbortController();
    discoverAbort.current = controller;
    setDiscovering(true);
    setError(null);
    try {
      const result = await apiFetch<Recommendation[]>(`/stems/jobs/${selected.id}/discover`, {
        method: "POST", body: JSON.stringify({ weights, limit: 12 }), signal: controller.signal,
      });
      setMixRecommendations((current) => {
        const previousRanks = new Map(current.map((item, index) => [item.song.id, index]));
        return result.map((item, index) => ({ ...item, rank_change: previousRanks.has(item.song.id) ? (previousRanks.get(item.song.id) ?? index) - index : 0 }));
      });
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (discoverAbort.current === controller) setDiscovering(false);
    }
  }

  async function interpretMix(prompt: string, weights: Record<string, number>) {
    if (!selected) throw new Error("Select a stem session first.");
    return apiFetch<{ weights: Record<string, number>; explanation: string }>(`/stems/jobs/${selected.id}/interpret-mix`, {
      method: "POST", body: JSON.stringify({ prompt, weights }),
    });
  }

  async function compareStemBrains(token: string) {
    if (!selected) throw new Error("Select a stem session first.");
    return apiFetch<{ compatibility: number | null; layers: Array<{ name: string; label: string; mine: number | null; theirs: number | null; connection: number | null }> }>(`/stems/jobs/${selected.id}/compare`, {
      method: "POST", body: JSON.stringify({ token }),
    });
  }

  async function addRecommendationToMap(recommendation: Recommendation) {
    setError(null);
    try {
      await apiFetch("/ingest/search", { method: "POST", body: JSON.stringify({ query: `${recommendation.song.title} — ${recommendation.song.artist}` }) });
      setMixRecommendations((current) => current.filter((item) => item.song.id !== recommendation.song.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function signOut() {
    clearSessionToken();
    router.push("/");
  }

  // Mirrors handleShowInBrain in app/page.tsx — that one just sets state directly since it's
  // already on the dashboard; from here (a separate route) getting there means a real
  // navigation, so the target song id rides along as a query param the dashboard reads once
  // on mount (see the `pulse` handling in app/page.tsx) and then clears from the URL.
  function showInBrain(songId: string) {
    router.push(`/?pulse=${encodeURIComponent(songId)}`);
  }

  // Resonance Threads need a real node to draw *from* — the song being separated has to
  // already be on the map. add-to-map already sets job.mapped_song_id, which the dashboard
  // uses as the thread source once it re-fetches this job's /xray itself (see app/page.tsx).
  function viewResonance(stemName: string) {
    if (!selected?.mapped_song_id) {
      setError("Add this song to your map first — resonance threads draw from its node.");
      return;
    }
    router.push(`/?resonance=${selected.id}&stem=${encodeURIComponent(stemName)}`);
  }

  if (authLoading) return <div className="flex min-h-screen items-center justify-center"><RefreshCw className="animate-spin text-[var(--accent)]" /></div>;
  if (!user) return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <Card className="max-w-md p-8 text-center"><SectionLabel>Authentication required</SectionLabel><h1 className="mt-3 text-2xl font-semibold">Sign in to use Stem Studio</h1><p className="mt-3 text-[var(--text-body)]">Stem files are private, quota-controlled, and tied to your Sonicmap account.</p><Link className="mt-6 inline-block text-[var(--accent-bright)]" href="/">Return to Sonicmap</Link></Card>
    </main>
  );

  return (
    <div className="min-h-screen bg-[var(--bg-base)]">
      <Nav user={user} onSignOut={signOut} />
      <main className="mx-auto max-w-[1240px] px-6 pb-16 pt-24">
        <Link href="/" className="inline-flex items-center gap-2 text-[12px] text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"><ArrowLeft size={14} /> Back to your brain</Link>
        <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
          <div><SectionLabel>Stem Studio</SectionLabel><h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em]">Take the mix apart.</h1><p className="mt-2 max-w-2xl text-[var(--text-body)]">Separate authorized music into synchronized, studio-ready instrument layers. Files automatically disappear after 24 hours.</p></div>
          <div className="font-mono text-[10px] tracking-[0.07em] text-[var(--text-tertiary)]">3 JOBS / 24H · 1 ACTIVE · 250 MB · 15 MIN</div>
        </div>

        {requestedSong && (
          <p className="mt-4 text-[12px] text-[var(--text-tertiary)]">
            Preparing to separate <span className="text-[var(--text-primary)]">{requestedSong.title}</span>
            {requestedSong.artist ? <> by <span className="text-[var(--text-primary)]">{requestedSong.artist}</span></> : null}
            {" — "}paste a link to it, or upload the audio, below.
          </p>
        )}

        <div className="mt-8 grid items-start gap-6 lg:grid-cols-[390px_1fr]">
          <div className="space-y-6 lg:sticky lg:top-24">
            <Card className="p-5">
              <div className="grid grid-cols-2 gap-2 rounded-[var(--r-md)] bg-black/20 p-1">
                <button onClick={() => setMode("upload")} className={`flex items-center justify-center gap-2 rounded-[var(--r-sm)] py-2 text-[12px] ${mode === "upload" ? "bg-white/[0.06] text-[var(--text-primary)]" : "text-[var(--text-tertiary)]"}`}><Upload size={14} /> Upload</button>
                <button onClick={() => setMode("youtube")} className={`flex items-center justify-center gap-2 rounded-[var(--r-sm)] py-2 text-[12px] ${mode === "youtube" ? "bg-white/[0.06] text-[var(--text-primary)]" : "text-[var(--text-tertiary)]"}`}><Video size={14} /> YouTube</button>
              </div>
              {mode === "upload" ? (
                <div onDragOver={(event: DragEvent) => event.preventDefault()} onDrop={(event: DragEvent) => { event.preventDefault(); chooseFile(event.dataTransfer.files[0] ?? null); }} onClick={() => fileInput.current?.click()} className="mt-4 cursor-pointer rounded-[var(--r-md)] border border-dashed border-[var(--border-medium)] px-5 py-10 text-center transition-colors hover:border-[var(--accent)]">
                  <input ref={fileInput} className="hidden" type="file" accept=".mp3,.m4a,.aac,.wav,.flac,.ogg,.opus,audio/*" onChange={(event: ChangeEvent<HTMLInputElement>) => chooseFile(event.target.files?.[0] ?? null)} />
                  <AudioLines className="mx-auto text-[var(--accent)]" strokeWidth={1.3} />
                  <p className="mt-3 truncate text-[13px] text-[var(--text-primary)]">{file?.name ?? "Drop audio or click to browse"}</p>
                  <p className="mt-1 font-mono text-[10px] text-[var(--text-tertiary)]">MP3 · M4A · WAV · FLAC · OGG · OPUS</p>
                </div>
              ) : <input value={youtubeUrl} onChange={(event) => setYoutubeUrl(event.target.value)} placeholder="https://youtube.com/watch?v=…" className="mt-4 w-full rounded-[var(--r-md)] border border-[var(--border-medium)] bg-black/20 px-4 py-3 text-[13px] outline-none focus:border-[var(--accent)]" />}

              <fieldset className="mt-5"><legend className="font-mono text-[10px] tracking-[0.07em] text-[var(--text-tertiary)]">SEPARATION MODEL</legend><div className="mt-2 grid grid-cols-2 gap-2">{(["4stem", "6stem"] as const).map((value) => <button key={value} onClick={() => setModel(value)} className={`rounded-[var(--r-md)] border p-3 text-left ${model === value ? "border-[var(--accent)] bg-[var(--accent-subtle)]" : "border-[var(--border-subtle)]"}`}><span className="block text-[13px] font-medium">{value === "4stem" ? "Standard · 4" : "Advanced · 6"}</span><span className="mt-1 block text-[10px] text-[var(--text-tertiary)]">{value === "4stem" ? "Faster, dependable" : "Adds piano + guitar"}</span></button>)}</div></fieldset>
              <label className="mt-5 flex cursor-pointer items-start gap-3 text-[12px] text-[var(--text-body)]"><input type="checkbox" checked={rights} onChange={(event) => setRights(event.target.checked)} className="mt-1 accent-[var(--accent)]" /><span>I own this audio or have permission to process it.</span></label>
              {uploadProgress !== null && <div className="mt-4"><div className="h-1 overflow-hidden rounded bg-white/[0.06]"><div className="h-full bg-[var(--accent)] transition-[width]" style={{ width: `${uploadProgress}%` }} /></div><p className="mt-1 text-right font-mono text-[10px] text-[var(--text-tertiary)]">UPLOADING {uploadProgress}%</p></div>}
              {error && <p role="alert" className="mt-4 flex gap-2 rounded-[var(--r-md)] bg-[var(--error-subtle)] p-3 text-[12px] text-[var(--error)]"><AlertCircle size={15} className="shrink-0" /> {error}</p>}
              <Button className="mt-5 w-full" variant="primary" loading={busy} disabled={!rights || (mode === "upload" ? !file : !youtubeUrl.trim())} onClick={createJob}>Separate stems</Button>
            </Card>

            {jobs.length > 0 && <Card className="p-4"><SectionLabel>Recent sessions</SectionLabel><div className="mt-3 space-y-1">{jobs.map((job) => <button key={job.id} onClick={() => setSelected(job)} className={`flex w-full items-center justify-between gap-3 rounded-[var(--r-sm)] px-3 py-2 text-left ${selected?.id === job.id ? "bg-white/[0.05]" : "hover:bg-white/[0.03]"}`}><span className="min-w-0 truncate text-[12px]">{job.source_name}</span><span className="shrink-0 font-mono text-[9px] uppercase text-[var(--text-tertiary)]">{job.status}</span></button>)}</div></Card>}
          </div>

          <section aria-live="polite">
            {manifest ? <StemMixer key={manifest.job.id} manifest={manifest} xray={xray} recommendations={mixRecommendations} discovering={discovering} onDiscover={discoverFromMix} onInterpret={interpretMix} onCompare={compareStemBrains} onAddRecommendation={addRecommendationToMap} onAddToMap={addSelectedToMap} onShowInBrain={showInBrain} onViewResonance={viewResonance} addingToMap={addingJobId === manifest.job.id} addedToMap={addedJobIds.has(manifest.job.id) || Boolean(manifest.job.mapped_song_id)} /> : selected ? (
              <Card className="flex min-h-[430px] flex-col items-center justify-center p-8 text-center">
                {selected.status === "failed" ? <AlertCircle size={34} className="text-[var(--error)]" /> : selected.status === "ready" ? <Check size={34} className="text-[var(--success)]" /> : selected.status === "cancelled" || selected.status === "expired" ? <Clock3 size={34} className="text-[var(--text-tertiary)]" /> : <div className="relative h-24 w-24 rounded-full border border-[var(--border-accent)]"><div className="absolute inset-2 animate-pulse rounded-full bg-[radial-gradient(circle,rgba(212,160,60,.25),transparent_68%)]" /><AudioLines className="absolute inset-0 m-auto text-[var(--accent-bright)]" /></div>}
                <h2 className="mt-5 text-xl font-semibold capitalize">{selected.status === "ready" ? "Preparing your mixer" : selected.stage}</h2>
                <p className="mt-2 max-w-md text-[13px] text-[var(--text-body)]">{selected.error_message ?? (ACTIVE.has(selected.status) ? "The worker is processing this away from the recommendation API, so your map stays responsive." : selected.status === "expired" ? "This session passed its private 24-hour retention window." : "This session was cancelled.")}</p>
                {ACTIVE.has(selected.status) && <><div className="mt-6 h-1.5 w-full max-w-sm overflow-hidden rounded bg-white/[0.06]"><div className="h-full bg-[var(--accent)] shadow-[0_0_12px_var(--accent)] transition-[width]" style={{ width: `${selected.progress}%` }} /></div><span className="mt-2 font-mono text-[10px] text-[var(--text-tertiary)]">{selected.progress}% · {selected.model === "6stem" ? "6-STEM ADVANCED" : "4-STEM STANDARD"}</span><Button className="mt-5" size="sm" variant="ghost" onClick={cancelJob}>Cancel job</Button></>}
              </Card>
            ) : <Card className="flex min-h-[430px] flex-col items-center justify-center p-8 text-center"><AudioLines size={36} className="text-[var(--accent)]" /><h2 className="mt-5 text-xl font-semibold">Your mixer will appear here</h2><p className="mt-2 max-w-sm text-[13px] text-[var(--text-body)]">Choose authorized audio, select a separation model, and start a private session.</p></Card>}
          </section>
        </div>
      </main>
    </div>
  );
}


/** Next 16 refuses to prerender a page that reads useSearchParams outside a Suspense
 * boundary — without this the production build fails on this route even though `next dev`
 * serves it happily. The fallback is the same quiet spinner the authenticating state uses,
 * so the boundary is invisible in practice. */
export default function StemStudioPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[var(--bg-base)]">
          <RefreshCw size={18} strokeWidth={1.5} className="animate-spin text-[var(--text-tertiary)]" />
        </div>
      }
    >
      <StemStudio />
    </Suspense>
  );
}
