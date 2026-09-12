"use client";

import { useState } from "react";
import { Copy, ExternalLink, GitCompare, Music2, Play, Search, Sparkles } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Recommendation, Song } from "@/lib/types";
import { Card, SectionLabel } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ExternalSongLinks } from "@/components/ui/ExternalSongLinks";

export function DiscoveryStudio({ recommendations, spotifyEnabled = true }: { recommendations: Recommendation[]; spotifyEnabled?: boolean }) {
  const [prompt, setPrompt] = useState("");
  const [results, setResults] = useState<Song[]>([]);
  const [note, setNote] = useState("");
  const [token, setToken] = useState("");
  const [compatibility, setCompatibility] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  async function discover() {
    if (!prompt.trim()) return;
    setBusy(true);
    try { const data = await apiFetch<{ interpretation: string; songs: Song[] }>("/studio/discover", { method: "POST", body: JSON.stringify({ prompt }) }); setResults(data.songs); setNote(data.interpretation); }
    catch (error) { setNote(error instanceof Error ? error.message : "Discovery failed. Please try again."); }
    finally { setBusy(false); }
  }

  async function share() {
    try {
      const data = await apiFetch<{ token: string }>("/studio/share", { method: "POST" });
      setToken(data.token); await navigator.clipboard.writeText(data.token); setNote("Private Brain Merge code copied.");
    } catch (error) { setNote(error instanceof Error ? error.message : "Could not create a share code."); }
  }

  async function compare() {
    if (!token.trim()) return;
    try {
      const data = await apiFetch<{ compatibility: number }>("/studio/compare", { method: "POST", body: JSON.stringify({ token: token.trim() }) });
      setCompatibility(data.compatibility);
    } catch (error) { setCompatibility(null); setNote(error instanceof Error ? error.message : "Brain Merge failed."); }
  }

  async function exportJourney() {
    const ids = recommendations.slice(0, 20).map(r => r.song.id);
    if (!ids.length) return;
    setBusy(true);
    try { const built = await apiFetch<{ songs: Song[] }>("/studio/journey", { method: "POST", body: JSON.stringify({ song_ids: ids, name: "My Sonicmap Journey" }) }); const data = await apiFetch<{ url: string }>("/studio/journey/export", { method: "POST", body: JSON.stringify({ song_ids: built.songs.map(s => s.id), name: "My Sonicmap Journey" }) }); window.open(data.url, "_blank", "noopener,noreferrer"); setNote("Taste journey exported to Spotify."); }
    catch (error) { setNote(error instanceof Error ? error.message : "Spotify export failed. Please try again."); }
    finally { setBusy(false); }
  }

  return <Card className="p-6">
    <div className="flex items-center gap-2"><Sparkles size={14} className="text-[var(--accent)]" /><SectionLabel>Discovery Studio</SectionLabel></div>
    <p className="mt-2 text-[12px] leading-relaxed text-[var(--text-tertiary)]">Describe a sound, mood, setting, or tempo. Your brain remains the ranking anchor.</p>
    <div className="mt-3 flex gap-2"><input value={prompt} onChange={e => setPrompt(e.target.value)} onKeyDown={e => e.key === "Enter" && void discover()} placeholder="Warm late-night electronic, under 110 BPM…" className="min-w-0 flex-1 rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-surface)] px-3 py-2 text-[12px] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]" /><Button size="sm" onClick={discover} loading={busy}><Search size={13} /></Button></div>
    {note && <p className="mt-2 text-[11px] text-[var(--text-tertiary)]">{note}</p>}
    {results.length > 0 && (
      <ol className="mt-3 space-y-2" aria-label="Discovered songs">
        {results.slice(0, 10).map((song) => (
          <li key={song.id} className="rounded-[var(--r-md)] border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3">
            <div className="flex items-start gap-2.5">
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[var(--accent-subtle)] text-[var(--accent)]" aria-hidden>
                <Music2 size={13} strokeWidth={1.5} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="break-words text-[12px] font-medium leading-[1.4] text-[var(--text-primary)]">{song.title}</p>
                <p className="mt-0.5 break-words text-[11px] leading-[1.4] text-[var(--text-body)]">{song.artist}</p>
              </div>
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--border-subtle)] pt-2">
              <p className="min-w-0 truncate font-mono text-[10px] text-[var(--text-tertiary)]">
                {[song.genre, song.bpm !== null ? `${Math.round(song.bpm)} BPM` : null].filter(Boolean).join(" · ") || "Taste-matched"}
              </p>
              <div className="flex shrink-0 items-center gap-2.5">
                {song.preview_url && (
                  <a
                    href={song.preview_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    aria-label={`Play preview of ${song.title}`}
                    title="Play preview"
                    className="text-[var(--text-tertiary)] transition-colors hover:text-[var(--accent)]"
                  >
                    <Play size={12} fill="currentColor" />
                  </a>
                )}
                <ExternalSongLinks title={song.title} artist={song.artist} size={12} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    )}
    <div className="mt-5 border-t border-[var(--border-subtle)] pt-4"><p className="text-[11px] font-medium uppercase tracking-wider text-[var(--text-tertiary)]">Brain Merge</p><div className="mt-2 flex gap-2"><input value={token} onChange={e => setToken(e.target.value)} placeholder="Paste a friend's private code" className="min-w-0 flex-1 rounded-[var(--r-md)] border border-[var(--border-medium)] bg-[var(--bg-surface)] px-3 py-2 text-[11px] text-[var(--text-primary)] outline-none" /><Button size="sm" variant="ghost" onClick={compare}><GitCompare size={13} /></Button></div>{compatibility !== null && <p className="mt-2 font-mono text-[20px] text-[var(--accent)]">{compatibility}% compatible</p>}<button onClick={() => void share()} className="mt-2 flex items-center gap-1 text-[11px] text-[var(--text-tertiary)] hover:text-[var(--accent)]"><Copy size={11} /> Create/copy my private code</button></div>
    <Button className="mt-5 w-full" variant="secondary" onClick={exportJourney} loading={busy} disabled={!spotifyEnabled || !recommendations.length} title={!spotifyEnabled ? "Sign in and connect Spotify to export" : undefined}><ExternalLink size={13} /> Export taste journey to Spotify</Button>
  </Card>;
}
