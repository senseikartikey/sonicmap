"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Brain, Check, Download, GitCompare, Pause, Play, Share2, Sparkles, Volume2, VolumeX } from "lucide-react";
import { Recommendation, StemManifest, StemXRay } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Card, SectionLabel } from "@/components/ui/Card";

type Mix = Record<string, { volume: number; muted: boolean; solo: boolean }>;

function Waveform({ url, onSeek }: { url?: string; onSeek: (fraction: number) => void }) {
  const [peaks, setPeaks] = useState<number[]>([]);
  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();
    fetch(url, { signal: controller.signal }).then((r) => r.json()).then((v) => setPeaks(v.peaks ?? [])).catch(() => {});
    return () => controller.abort();
  }, [url]);
  const path = useMemo(() => {
    if (!peaks.length) return "";
    return peaks.map((peak, i) => `${i ? "L" : "M"}${(i / Math.max(1, peaks.length - 1)) * 100},${12 - peak * 10}`).join(" ") +
      peaks.slice().reverse().map((peak, index) => ` L${((peaks.length - 1 - index) / Math.max(1, peaks.length - 1)) * 100},${12 + peak * 10}`).join("") + " Z";
  }, [peaks]);
  return (
    <svg
      viewBox="0 0 100 24" preserveAspectRatio="none" aria-label="Seek waveform"
      className="h-10 min-w-0 flex-1 cursor-pointer rounded bg-black/20"
      onClick={(event) => onSeek(event.nativeEvent.offsetX / event.currentTarget.clientWidth)}
    >
      {path && <path d={path} fill="rgba(212,160,60,.34)" />}
    </svg>
  );
}

export function StemMixer({
  manifest,
  onAddToMap,
  addingToMap = false,
  addedToMap = false,
  xray,
  recommendations = [],
  discovering = false,
  onDiscover,
  onAddRecommendation,
  onInterpret,
  onCompare,
  onShowInBrain,
  onViewResonance,
}: {
  manifest: StemManifest;
  onAddToMap?: () => void;
  addingToMap?: boolean;
  addedToMap?: boolean;
  xray?: StemXRay | null;
  recommendations?: Recommendation[];
  discovering?: boolean;
  onDiscover?: (weights: Record<string, number>) => void;
  onAddRecommendation?: (recommendation: Recommendation) => void;
  onInterpret?: (prompt: string, weights: Record<string, number>) => Promise<{ weights: Record<string, number>; explanation: string }>;
  onCompare?: (token: string) => Promise<{ compatibility: number | null; layers: Array<{ name: string; label: string; mine: number | null; theirs: number | null; connection: number | null }> }>;
  /** Jumps back to the main dashboard's 3D brain and pulses the given song's node — reuses the
   * exact mechanism RecommendationsPanel's own "Show in brain" already drives there
   * (page.tsx's pulseFromSongId), just triggered from this separate route via a `?pulse=`
   * param since Stem Studio and the brain view live on different pages. */
  onShowInBrain?: (songId: string) => void;
  /** Jumps to the brain and draws this one stem's Resonance Threads — real cross-cluster
   * matches on this isolated layer (see stem_intelligence.py's build_xray) — as glowing lines
   * from the source song to each match. Only offered once the song this session is separating
   * has actually been added to the map (onAddToMap) — threads need a real node to draw from. */
  onViewResonance?: (stemName: string) => void;
}) {
  const names = useMemo(() => manifest.stems.map((stem) => stem.name), [manifest.stems]);
  const [mix, setMix] = useState<Mix>(() => Object.fromEntries(names.map((name) => [name, { volume: 1, muted: false, solo: false }])));
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [prompt, setPrompt] = useState("");
  const [promptNote, setPromptNote] = useState<string | null>(null);
  const [interpreting, setInterpreting] = useState(false);
  const [compareToken, setCompareToken] = useState("");
  const [comparison, setComparison] = useState<Awaited<ReturnType<NonNullable<typeof onCompare>>> | null>(null);
  const audio = useRef<Record<string, HTMLAudioElement>>({});
  const userAdjusted = useRef(false);
  const discoverRef = useRef(onDiscover);
  const masterName = names[0];
  const duration = manifest.job.duration_seconds ?? 0;
  const anySolo = Object.values(mix).some((state) => state.solo);

  // Kept in sync via an effect rather than assigned during render: a render can run without
  // committing (e.g. under Suspense or other concurrent-rendering interruptions), and mutating
  // a ref as a side effect of that phantom render would drift it from what actually committed.
  useEffect(() => {
    discoverRef.current = onDiscover;
  }, [onDiscover]);

  useEffect(() => {
    for (const name of names) {
      const element = audio.current[name];
      const state = mix[name];
      if (element && state) element.volume = state.muted || (anySolo && !state.solo) ? 0 : state.volume;
    }
  }, [anySolo, mix, names]);

  async function togglePlayback() {
    const elements = names.map((name) => audio.current[name]).filter(Boolean);
    if (playing) {
      elements.forEach((element) => element.pause());
      setPlaying(false);
      return;
    }
    const master = audio.current[masterName];
    if (!master) return;
    const target = master.currentTime;
    elements.forEach((element) => { element.currentTime = target; });
    try {
      await Promise.all(elements.map((element) => element.play()));
      setPlaying(true);
    } catch {
      elements.forEach((element) => element.pause());
    }
  }

  function seek(fraction: number) {
    const next = Math.max(0, Math.min(duration, fraction * duration));
    names.forEach((name) => { if (audio.current[name]) audio.current[name].currentTime = next; });
    setTime(next);
  }

  function patch(name: string, next: Partial<Mix[string]>) {
    userAdjusted.current = true;
    setMix((previous) => ({ ...previous, [name]: { ...previous[name], ...next } }));
  }

  function preset(kind: "all" | "karaoke" | "acapella" | "rhythm") {
    userAdjusted.current = true;
    setMix(Object.fromEntries(names.map((name) => [name, {
      volume: 1,
      muted: kind === "karaoke" ? name === "vocals" : kind === "acapella" ? name !== "vocals" : kind === "rhythm" ? !["drums", "bass"].includes(name) : false,
      solo: false,
    }])));
  }

  const audibleWeights = useMemo(() => Object.fromEntries(names.map((name) => {
    const state = mix[name];
    const audible = state && !state.muted && (!anySolo || state.solo);
    return [name, audible ? state.volume : 0];
  })), [anySolo, mix, names]);

  useEffect(() => {
    if (!userAdjusted.current || !discoverRef.current) return;
    const timer = window.setTimeout(() => discoverRef.current?.(audibleWeights), 800);
    return () => window.clearTimeout(timer);
  }, [audibleWeights]);

  function discover() {
    if (!onDiscover) return;
    onDiscover(audibleWeights);
  }

  async function applyPrompt() {
    if (!onInterpret || !prompt.trim()) return;
    setInterpreting(true);
    setPromptNote(null);
    try {
      const result = await onInterpret(prompt.trim(), audibleWeights);
      userAdjusted.current = true;
      setMix((previous) => Object.fromEntries(names.map((name) => [name, {
        ...previous[name], volume: result.weights[name] ?? 0, muted: (result.weights[name] ?? 0) === 0, solo: false,
      }])));
      setPromptNote(result.explanation);
    } catch (reason) {
      setPromptNote(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setInterpreting(false);
    }
  }

  async function compareBrains() {
    if (!onCompare || !compareToken.trim()) return;
    setComparison(await onCompare(compareToken.trim()));
  }

  async function shareXRay() {
    if (!xray) return;
    const strongest = [...xray.stems].sort((a, b) => (b.match ?? 0) - (a.match ?? 0))[0];
    const text = `${manifest.job.source_name}\n${xray.overall_match ?? "—"}% Sonicmap brain match${strongest ? `\nStrongest layer: ${strongest.label} (${strongest.match ?? "—"}%)` : ""}`;
    const canvas = document.createElement("canvas");
    canvas.width = 1200; canvas.height = 630;
    const context = canvas.getContext("2d");
    if (!context) return;
    const gradient = context.createRadialGradient(160, 80, 10, 500, 300, 900);
    gradient.addColorStop(0, "#2d2111"); gradient.addColorStop(0.55, "#100d09"); gradient.addColorStop(1, "#050505");
    context.fillStyle = gradient; context.fillRect(0, 0, 1200, 630);
    context.strokeStyle = "rgba(226,178,79,.35)"; context.lineWidth = 2; context.strokeRect(38, 38, 1124, 554);
    context.fillStyle = "#e2b24f"; context.font = "600 24px monospace"; context.fillText("SONICMAP · TASTE X-RAY", 78, 92);
    context.fillStyle = "#fff8e8"; context.font = "600 42px sans-serif"; context.fillText((manifest.job.source_name ?? "My mix").slice(0, 42), 78, 158);
    context.fillStyle = "#e2b24f"; context.font = "700 108px sans-serif"; context.fillText(`${xray.overall_match ?? "—"}%`, 78, 300);
    context.fillStyle = "#9d9485"; context.font = "20px monospace"; context.fillText("BRAIN COMPATIBILITY", 84, 338);
    xray.stems.slice(0, 6).forEach((stem, index) => {
      const x = 600, y = 110 + index * 70, width = 450;
      context.fillStyle = "#f5ead5"; context.font = "18px sans-serif"; context.fillText(stem.label, x, y);
      context.fillStyle = "#e2b24f"; context.font = "18px monospace"; context.fillText(`${stem.match ?? "—"}%`, 1070, y);
      context.fillStyle = "rgba(255,255,255,.08)"; context.fillRect(x, y + 14, width, 7);
      context.fillStyle = "#c99338"; context.fillRect(x, y + 14, width * ((stem.match ?? 0) / 100), 7);
    });
    context.fillStyle = "#8c8376"; context.font = "16px sans-serif"; context.fillText("The musical layers that make this brain light up.", 78, 550);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
    if (!blob) return;
    const file = new File([blob], "sonicmap-taste-xray.png", { type: "image/png" });
    if (navigator.share && navigator.canShare?.({ files: [file] })) await navigator.share({ title: "My Sonicmap Taste X-Ray", text, files: [file] });
    else { const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = file.name; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
  }

  return (
    <Card className="p-5 sm:p-7">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <SectionLabel>Stem mixer{manifest.job.reused ? " · instant fingerprint reuse" : ""}</SectionLabel>
          <h2 className="mt-2 text-xl font-semibold text-[var(--text-primary)]">{manifest.job.source_name}</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          {onAddToMap && (
            <Button size="sm" variant="ghost" onClick={onAddToMap} loading={addingToMap} disabled={addingToMap || addedToMap}>
              {addedToMap ? <Check size={14} /> : <Brain size={14} />} {addedToMap ? "Added to map" : "Add to map"}
            </Button>
          )}
          {(["all", "karaoke", "acapella", "rhythm"] as const).map((name) => (
            <Button key={name} size="sm" variant="ghost" onClick={() => preset(name)}>{name === "rhythm" ? "Drums + Bass" : name[0].toUpperCase() + name.slice(1)}</Button>
          ))}
          {manifest.archive_url && <a className="inline-flex items-center gap-2 rounded-[var(--r-md)] border border-[var(--border-medium)] px-[18px] py-2 text-[13px] font-semibold hover:border-[var(--accent)]" href={manifest.archive_url}><Download size={14} /> ZIP</a>}
        </div>
      </div>

      <div className="mt-6 flex items-center gap-4">
        <Button variant="primary" onClick={togglePlayback} aria-label={playing ? "Pause stems" : "Play stems"}>
          {playing ? <Pause size={16} /> : <Play size={16} />} {playing ? "Pause" : "Play"}
        </Button>
        <input aria-label="Track position" type="range" min={0} max={duration || 1} step="0.05" value={Math.min(time, duration || 1)} onChange={(event) => seek(Number(event.target.value) / (duration || 1))} className="min-w-0 flex-1 accent-[var(--accent)]" />
        <span className="w-24 text-right font-mono text-[11px] text-[var(--text-tertiary)]">{Math.floor(time / 60)}:{String(Math.floor(time % 60)).padStart(2, "0")} / {Math.floor(duration / 60)}:{String(Math.floor(duration % 60)).padStart(2, "0")}</span>
      </div>

      {xray && (
        <section className="mt-7 overflow-hidden rounded-[var(--r-lg)] border border-[var(--border-accent)] bg-[radial-gradient(circle_at_top_left,rgba(212,160,60,.12),transparent_42%),rgba(0,0,0,.16)] p-5" aria-label="Taste X-Ray">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div><SectionLabel>Taste X-Ray</SectionLabel><h3 className="mt-2 text-lg font-semibold">Why your brain responds</h3><p className="mt-1 text-[12px] text-[var(--text-body)]">Each layer is compared with the real audio fingerprint of songs already inside your brain.</p></div>
            <div className="flex items-center gap-3"><div className="text-right"><div className="font-mono text-3xl text-[var(--accent-bright)]">{xray.overall_match ?? "—"}%</div><div className="font-mono text-[9px] tracking-[.08em] text-[var(--text-tertiary)]">BRAIN COMPATIBILITY</div></div><Button size="sm" variant="ghost" onClick={shareXRay} aria-label="Share Taste X-Ray"><Share2 size={14} /></Button></div>
          </div>
          <div className="mt-5 grid gap-2 sm:grid-cols-2">
            {xray.stems.map((stem) => <div key={stem.name} className="rounded-[var(--r-md)] border border-white/[.05] bg-black/20 p-3"><div className="flex items-center justify-between gap-3"><span className="text-[12px] font-medium">{stem.label}</span><span className="font-mono text-[12px] text-[var(--accent-bright)]">{stem.match ?? "—"}%</span></div><div className="mt-2 h-1 overflow-hidden rounded bg-white/[.06]"><div className="h-full bg-[var(--accent)]" style={{ width: `${stem.match ?? 0}%` }} /></div><div className="mt-2 flex items-center justify-between gap-2"><p className="min-w-0 truncate text-[10px] text-[var(--text-tertiary)]">{stem.best_match ? `Closest to ${stem.best_match.title} · ${stem.best_match.artist}` : `${stem.prominence}% of this mix · add analyzed songs to sharpen it`}</p>{stem.best_match && onShowInBrain && <button onClick={() => onShowInBrain(stem.best_match!.id)} aria-label={`Show ${stem.best_match.title} in the brain view`} className="shrink-0 text-[var(--text-tertiary)] transition-colors hover:text-[var(--accent)]"><Brain size={12} strokeWidth={1.5} /></button>}</div>{stem.threads.length > 0 && onViewResonance && <button onClick={() => onViewResonance(stem.name)} className="mt-2 inline-flex items-center gap-1 text-[10px] text-[#c68aff] hover:text-[var(--text-primary)]"><Sparkles size={10} /> {stem.threads.length} cross-cluster match{stem.threads.length === 1 ? "" : "es"} · view resonance</button>}</div>)}
          </div>
        </section>
      )}

      <div className="mt-7 space-y-3">
        {manifest.stems.map((stem) => {
          const state = mix[stem.name] ?? { volume: 1, muted: false, solo: false };
          return (
            <div key={stem.name} className="grid items-center gap-3 rounded-[var(--r-md)] bg-black/15 p-3 sm:grid-cols-[90px_1fr_130px_auto_auto_auto]">
              <span className="font-mono text-[12px] uppercase tracking-[0.08em] text-[var(--text-primary)]">{stem.name}</span>
              <Waveform url={stem.waveform_url} onSeek={seek} />
              <label className="flex items-center gap-2 text-[11px] text-[var(--text-tertiary)]"><Volume2 size={13} /><input aria-label={`${stem.name} volume`} type="range" min={0} max={1} step={0.01} value={state.volume} onChange={(event) => patch(stem.name, { volume: Number(event.target.value) })} className="w-full accent-[var(--accent)]" /></label>
              <button onClick={() => patch(stem.name, { muted: !state.muted })} className={`rounded px-2 py-1 font-mono text-[10px] ${state.muted ? "bg-[var(--error-subtle)] text-[var(--error)]" : "text-[var(--text-tertiary)]"}`}>{state.muted ? <VolumeX size={14} /> : "M"}</button>
              <button onClick={() => patch(stem.name, { solo: !state.solo })} className={`rounded px-2 py-1 font-mono text-[10px] ${state.solo ? "bg-[var(--accent-subtle)] text-[var(--accent-bright)]" : "text-[var(--text-tertiary)]"}`}>S</button>
              {stem.download_url && <a href={stem.download_url} aria-label={`Download ${stem.name}`} className="text-[var(--text-tertiary)] hover:text-[var(--accent-bright)]"><Download size={15} /></a>}
              {stem.preview_url && <audio ref={(element) => { if (element) audio.current[stem.name] = element; }} src={stem.preview_url} preload="metadata" onTimeUpdate={stem.name === masterName ? (event) => {
                const master = event.currentTarget;
                setTime(master.currentTime);
                names.forEach((name) => { const other = audio.current[name]; if (other && name !== masterName && Math.abs(other.currentTime - master.currentTime) > 0.05) other.currentTime = master.currentTime; });
              } : undefined} onEnded={stem.name === masterName ? () => setPlaying(false) : undefined} />}
            </div>
          );
        })}
      </div>

      {onDiscover && <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-[var(--r-md)] border border-[var(--border-medium)] p-4"><div><p className="text-[13px] font-medium">Turn this exact mix into discovery</p><p className="mt-1 text-[11px] text-[var(--text-tertiary)]">Mute, solo or rebalance layers, then let your brain find the nearest songs.</p></div><Button variant="primary" onClick={discover} loading={discovering}><Sparkles size={15} /> Discover from this mix</Button></div>}

      {onInterpret && <div className="mt-3 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-black/15 p-4"><label htmlFor="stem-prompt" className="text-[11px] font-medium text-[var(--text-body)]">Describe the sound you want</label><div className="mt-2 flex gap-2"><input id="stem-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void applyPrompt(); }} placeholder="Keep the drums and bass, remove the vocals" className="min-w-0 flex-1 rounded-[var(--r-md)] border border-[var(--border-medium)] bg-black/20 px-3 py-2 text-[12px] outline-none focus:border-[var(--accent)]" /><Button size="sm" variant="ghost" loading={interpreting} onClick={applyPrompt}><Sparkles size={13} /> Apply</Button></div>{promptNote && <p className="mt-2 text-[10px] text-[var(--text-tertiary)]">{promptNote}</p>}</div>}

      {onCompare && <div className="mt-3 rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-black/15 p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="text-[11px] font-medium text-[var(--text-body)]">Which layers connect your brains?</p><p className="mt-1 text-[10px] text-[var(--text-tertiary)]">Use a friend’s private Brain Merge code.</p></div><div className="flex gap-2"><input aria-label="Friend's private brain code" value={compareToken} onChange={(event) => setCompareToken(event.target.value)} placeholder="Private code" className="w-40 rounded-[var(--r-md)] border border-[var(--border-medium)] bg-black/20 px-3 py-2 text-[11px] outline-none focus:border-[var(--accent)]" /><Button size="sm" variant="ghost" onClick={() => void compareBrains()}><GitCompare size={13} /> Compare</Button></div></div>{comparison && <div className="mt-3"><p className="font-mono text-xl text-[var(--accent-bright)]">{comparison.compatibility ?? "—"}% LAYER CONNECTION</p><div className="mt-2 flex flex-wrap gap-2">{comparison.layers.map((layer) => <span key={layer.name} className="rounded-full border border-white/[.07] px-2 py-1 text-[9px] text-[var(--text-tertiary)]">{layer.label} · {layer.connection ?? "—"}%</span>)}</div></div>}</div>}

      {recommendations.length > 0 && <section className="mt-7"><SectionLabel>Mix-matched discoveries</SectionLabel><div className="mt-3 grid gap-2 sm:grid-cols-2">{recommendations.map((recommendation) => <article key={recommendation.song.id} className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-black/15 p-3"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-[13px] font-medium">{recommendation.song.title}</p><p className="truncate text-[11px] text-[var(--text-tertiary)]">{recommendation.song.artist}</p></div><div className="flex shrink-0 items-center gap-1">{recommendation.rank_change ? <span className={`inline-flex items-center text-[9px] ${recommendation.rank_change > 0 ? "text-[var(--success)]" : "text-[var(--error)]"}`}>{recommendation.rank_change > 0 ? <ArrowUp size={10} /> : <ArrowDown size={10} />}{Math.abs(recommendation.rank_change)}</span> : null}<span className="font-mono text-[10px] text-[var(--accent-bright)]">{Math.round(100 * Math.exp(-recommendation.distance * 1.15))}%</span></div></div><p className="mt-2 line-clamp-2 text-[10px] text-[var(--text-tertiary)]">{recommendation.reason}</p>{onAddRecommendation && <button onClick={() => onAddRecommendation(recommendation)} className="mt-2 inline-flex items-center gap-1 text-[10px] text-[var(--accent-bright)] hover:text-[var(--text-primary)]"><Brain size={11} /> Add to map</button>}{onShowInBrain && <button onClick={() => onShowInBrain(recommendation.best_match_song_id)} className="mt-2 ml-3 inline-flex items-center gap-1 text-[10px] text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"><Sparkles size={11} /> Show match in brain</button>}</article>)}</div></section>}
    </Card>
  );
}
